import { parseV7Command } from './core-chat-v7-contract.js';
import { parseV6Command } from './core-chat-v6-contract.js';
import { parseCoreCommand } from './core-chat-execution-contract.js';
import { parseV5Command } from './core-chat-v5-contract.js';
import { V5CommandStore } from './core-chat-v5-store.js';
import { startV5Execution, v5PtyOptions } from './core-chat-v5-runtime.js';
import { timingSafeStrEqual } from '../../web/ws-server.js';
import { CoreChatV5Service } from './core-chat-v5-service.js';
import { RecoveryLedger } from '../../bridge/execution-recovery-ledger.js';
import { ExecutorStopVerifier } from '../../bridge/executor-stop-verifier.js';
export async function recoverV5Http(ctx, req, value) {
    const bearer = req.headers.authorization?.match(/^Bearer\s+(.+)$/i)?.[1];
    if (!ctx.coreChatV5MachineSecret || !timingSafeStrEqual(bearer, ctx.coreChatV5MachineSecret))
        return { status: 401, body: { error: 'Unauthorized' } };
    const dependency = ctx.coreChatV5;
    if (!dependency)
        return { status: 503, body: { error: 'v5 runtime unavailable' } };
    let command, stop;
    try {
        if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).sort().join(',') !== 'command,stop')
            throw new Error();
        const request = value;
        if (typeof request.stop !== 'boolean')
            throw new Error();
        stop = request.stop;
        command = parseCoreCommand(request.command);
        if (new URL(command.eventCallbackUrl).origin !== dependency.trustedCallbackOrigin)
            throw new Error();
    }
    catch {
        return { status: 400, body: { error: 'v5 recovery invalid' } };
    }
    try {
        const store = new V5CommandStore(dependency.runtime);
        let found = await store.inspectBound(command);
        if (found && command.contractVersion !== 'core_chat_collaboration_v5' && !stop) {
            const rotation = await dependency.runtime.transaction(client => store.rotateTransport(client, command, dependency.trustedCallbackOrigin));
            if (rotation.kind === 'conflict' || !dependency.prepareV6)
                throw new Error('HR recovery capability unavailable');
            if (rotation.kind === 'rotated')
                await dependency.prepareV6(command); // Credentials only; never accepts or starts native execution.
            found = await store.inspectBound(command);
        }
        if (found?.acceptance.launchLeaseEpoch !== null && found) {
            if (stop)
                await dependency.requestStop?.(found.command);
            await new RecoveryLedger(dependency.runtime).verifyStopped({ ...found.command, leaseEpoch: found.acceptance.launchLeaseEpoch }, new ExecutorStopVerifier());
            found = await store.inspectBound(command);
        }
        return { status: 200, body: {
                version: 'hr_bound_recovery_v1', commandId: command.commandId, runId: command.runId,
                commandHash: command.commandHash, missing: found === null,
                acceptance: found ? { contractVersion: command.contractVersion, commandId: command.commandId,
                    runId: command.runId, commandSeq: command.commandSeq, status: 'accepted', duplicate: true,
                    executionState: 'accepted', acceptance: found.acceptance } : null,
                recovery: found?.recovery ?? null,
            } };
    }
    catch {
        return { status: 503, body: { error: 'v5 recovery unavailable' } };
    }
}
export async function readinessV5Http(ctx, req, version = 'v5') {
    const bearer = req.headers.authorization?.match(/^Bearer\s+(.+)$/i)?.[1];
    if (!ctx.coreChatV5MachineSecret || !timingSafeStrEqual(bearer, ctx.coreChatV5MachineSecret))
        return { status: 401, body: { error: 'Unauthorized' } };
    if (!(ctx.coreChatV5 instanceof CoreChatV5Service))
        return { status: 503, body: { error: 'v5 service unavailable' } };
    if (version !== (ctx.coreChatV5.hrV6 ? (ctx.coreChatV5.hrV6.version ?? 'v6') : 'v5'))
        return { status: 503, body: { error: 'HR contract unavailable' } };
    const bot = ctx.registry.get('hr-bot');
    if (!bot)
        return { status: 503, body: { error: 'v5 service unavailable' } };
    try {
        return { status: 200, body: await ctx.coreChatV5.probe(bot, ctx.logger, ctx.circuitBreaker.isAvailableReadOnly('hr-bot') && ctx.budgetManager.canAcceptTaskReadOnly('hr-bot')) };
    }
    catch {
        return { status: 503, body: { error: 'v5 service unavailable' } };
    }
}
function hasUnsupportedPtyControl(input) {
    for (const character of input) {
        const codePoint = character.codePointAt(0);
        if ((codePoint <= 0x1f && codePoint !== 0x0a) || codePoint === 0x7f)
            return true;
    }
    return false;
}
export async function acceptV5Http(ctx, req, value, version = 'v5') {
    const dependency = ctx.coreChatV5;
    if (!dependency)
        return { status: 503, body: { error: 'v5 runtime unavailable' } };
    const bearer = req.headers.authorization?.match(/^Bearer\s+(.+)$/i)?.[1];
    if (!ctx.coreChatV5MachineSecret || !timingSafeStrEqual(bearer, ctx.coreChatV5MachineSecret))
        return { status: 401, body: { error: 'Unauthorized' } };
    let command;
    try {
        command = version === 'v7' ? parseV7Command(value) : version === 'v6' ? parseV6Command(value) : parseV5Command(value);
        if (dependency instanceof CoreChatV5Service && dependency.hrV6 && version !== (dependency.hrV6.version ?? 'v6'))
            return { status: 409, body: { error: 'HR dispatch version retired' } };
        if (version === 'v5' && dependency.prepareV6)
            return { status: 409, body: { error: 'HR legacy dispatch retired' } };
    }
    catch {
        return { status: 400, body: { error: 'v5 command invalid' } };
    }
    if (new URL(command.eventCallbackUrl).origin !== dependency.trustedCallbackOrigin)
        return { status: 403, body: { error: 'v5 callback forbidden' } };
    const store = new V5CommandStore(dependency.runtime);
    let rejected;
    let inputs;
    let outputs;
    let v6;
    const reject = (status, error) => {
        rejected = { status, body: { error } };
        throw new Error(error);
    };
    try {
        if (command.contractVersion !== 'core_chat_collaboration_v5') {
            if (!dependency.prepareV6)
                return reject(503, 'v6 runtime unavailable');
            v6 = await dependency.prepareV6(command);
        }
        if (command.inputAttachmentGrants.length && !(await store.inspectBound(command))) {
            if (!dependency.prepareInputs)
                return reject(503, 'v5 attachment execution unavailable');
            // Reversible network/file work owns no acceptance transaction or PG lock.
            // Concurrent contenders download separately; only the store may claim.
            try {
                inputs = await dependency.prepareInputs(command);
            }
            catch {
                return reject(503, 'v5 attachment execution unavailable');
            }
        }
        if (command.outputWriteGrant && dependency.prepareOutputs && !(await store.inspectBound(command))) {
            try {
                outputs = await dependency.prepareOutputs(command);
            }
            catch {
                return reject(503, 'v5 output files unavailable');
            }
        }
        const accepted = await dependency.runtime.transaction(async (client) => {
            // Serialize the short acceptance transaction across HTTP sessions for the
            // configured Bot capacity check. No executor/network operation holds this lock.
            await client.query("SELECT pg_advisory_xact_lock(hashtext('hr_runtime.v5.http.accept'))");
            const result = await store.acceptInTransaction(client, command);
            if (result.kind === 'conflict')
                return reject(409, 'v5 command conflict');
            let options;
            if (result.kind === 'new') {
                const bot = ctx.registry.get(command.targetBot);
                if (!bot)
                    return reject(404, 'v5 Bot unavailable');
                if (command.permissionScope.toolPolicy !== (bot.config.claude.toolPolicy ?? 'default'))
                    return reject(403, 'v5 permission forbidden');
                if (command.inputAttachmentGrants.length && (!inputs || command.inputAttachmentGrants.some(grant => Date.parse(grant.expiresAt) <= Date.now())))
                    return reject(503, 'v5 attachment execution unavailable');
                // The wire permits strings that the current interactive PTY transport
                // cannot submit literally. LF is its supported multiline control.
                if (!command.prompt.trim() || hasUnsupportedPtyControl(command.prompt))
                    return reject(503, 'v5 prompt execution unavailable');
                if (!ctx.circuitBreaker.isAvailable(command.targetBot))
                    return reject(503, 'v5 Bot unavailable');
                if (!ctx.budgetManager.canAcceptTask(command.targetBot).allowed)
                    return reject(429, 'v5 capacity unavailable');
                const configured = bot.config.persistentExecutor?.maxConcurrent ??
                    (process.env.METABOT_PERSISTENT_EXECUTOR_MAX_CONCURRENT
                        ? Number(process.env.METABOT_PERSISTENT_EXECUTOR_MAX_CONCURRENT)
                        : 20);
                if (!Number.isSafeInteger(configured) || configured < 1)
                    return reject(503, 'v5 runtime unavailable');
                const active = await client.query('SELECT count(*)::int AS count FROM hr_runtime.sessions WHERE target_bot=$1 AND active_command_id IS NOT NULL', [command.targetBot]);
                if (active.rows[0].count > configured)
                    return reject(429, 'v5 capacity unavailable');
                try {
                    options = v5PtyOptions(bot, ctx.logger);
                    if (v6)
                        options = { ...options, cwd: v6.cwd, resume: undefined, mcpConfigPath: v6.tools.mcpConfigPath, env: { ...options.env, HR_PLATFORM_CAPABILITY_FILE: v6.tools.capabilityPath } };
                }
                catch {
                    return reject(503, 'v5 runtime unavailable');
                }
            }
            const rotation = await store.rotateTransport(client, command, dependency.trustedCallbackOrigin);
            if (rotation.kind === 'conflict')
                return reject(409, 'v5 transport conflict');
            const claim = result.kind === 'new' ? await store.claimExecution(client, command.commandId) : undefined;
            const acceptance = await store.acceptanceAttestation(client, command.commandId);
            return { result, claim, options, acceptance };
        });
        let executionState = accepted.claim?.kind ?? 'accepted';
        if (accepted.claim?.kind === 'claimed') {
            try {
                const ownedInputs = inputs;
                const ownedOutputs = outputs;
                inputs = undefined; // Native lifecycle owns this bundle from this point.
                outputs = undefined; // Once launched, text/stop/error never deletes unresolved output.
                startV5Execution(accepted.claim.command, accepted.options, dependency.ownExecution, dependency.runtime, ownedInputs, ownedOutputs, v6?.tools);
            }
            catch {
                // Acceptance committed. Unknown launch/owner state remains claimed for M04.
                executionState = 'reconciliation_required';
                ctx.logger.warn({ commandId: command.commandId }, 'v5 execution entry requires reconciliation');
            }
        }
        return {
            status: 202,
            body: {
                contractVersion: command.contractVersion,
                commandId: command.commandId,
                runId: command.runId,
                commandSeq: accepted.result.commandSeq,
                status: 'accepted',
                duplicate: accepted.result.kind === 'duplicate',
                executionState,
                acceptance: accepted.acceptance,
            },
        };
    }
    catch {
        return rejected ?? { status: 503, body: { error: 'v5 runtime unavailable' } };
    }
    finally {
        // Rejected/duplicate contenders clean only their own unused bundle.
        await inputs?.cleanup().catch(() => undefined);
        await outputs?.discardUnused().catch(() => undefined);
    }
}
//# sourceMappingURL=core-chat-v5-routes.js.map