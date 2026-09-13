import { canonical } from '../api/routes/core-chat-v5-contract.js';
import { randomUUID } from 'node:crypto';
import { V5CommandStore } from '../api/routes/core-chat-v5-store.js';
export class RecoveryLedger {
    runtime;
    constructor(runtime) {
        this.runtime = runtime;
    }
    async recordOutput(binding) {
        await this.runtime.transaction(async (client) => {
            if (!(await this.lock(client, binding)))
                throw new Error('recovery binding invalid');
            await client.query(`INSERT INTO hr_runtime.recovery_evidence(command_id,has_output) VALUES ($1,true)
         ON CONFLICT(command_id) DO UPDATE SET has_output=true`, [binding.commandId]);
        });
    }
    async snapshot(binding) {
        return this.runtime.transaction(async (client) => {
            const command = await this.lock(client, binding);
            if (!command)
                return null;
            const rows = (await client.query(`SELECT c.command_id,e.* FROM hr_runtime.commands c LEFT JOIN hr_runtime.recovery_evidence e USING(command_id)
         WHERE c.turn_id=$1 AND c.logical_session_id=$2`, [binding.turnId, command.logical_session_id])).rows;
            const own = rows.find((row) => row.command_id === binding.commandId);
            const rank = Math.max(-1, ...rows.map((row) => row.effect_rank ?? -1));
            const complete = rows.every((row) => row.evidence_complete === true);
            return {
                evidenceComplete: complete,
                toolEffect: rank > 0 ? 'write' : complete && rank === 0 ? 'read_only' : 'unknown',
                hasOutput: rows.some((row) => row.has_output === true),
                executorStopped: !!own?.stop_proof_ref,
                executorStopProofRef: own?.stop_proof_ref ?? null,
                replayUsed: false,
            };
        });
    }
    async verifyStopped(binding, verifier) {
        const owned = await this.runtime.transaction(async (client) => {
            if (!(await this.lock(client, binding)))
                return [];
            return (await client.query('SELECT executor_ref,identity_json,exited_at FROM hr_runtime.executor_ownership WHERE command_id=$1', [binding.commandId])).rows;
        });
        if (!owned.length)
            return null;
        let proof = null;
        for (const row of owned) {
            const identity = JSON.parse(row.identity_json);
            // External process lookup never holds a PG lock. Persisting rechecks the exact binding.
            if (!row.exited_at && (await verifier.verify(identity)).kind !== 'stopped')
                continue;
            proof = await this.recordNativeExit(binding, row.executor_ref, identity);
        }
        return proof;
    }
    async recordEffect(turnId, commandId, effect) {
        const rank = ['read_only', 'local_idempotent', 'external_side_effect'].indexOf(effect);
        if (rank < 0)
            throw new Error('recovery effect invalid');
        await this.runtime.transaction(async (client) => {
            const command = await new V5CommandStore(this.runtime).lockCommand(client, commandId);
            if (!command || command.turn_id !== turnId)
                throw new Error('recovery binding invalid');
            await client.query(`INSERT INTO hr_runtime.recovery_evidence(command_id,effect_rank) VALUES ($1,$2)
         ON CONFLICT(command_id) DO UPDATE SET effect_rank=GREATEST(hr_runtime.recovery_evidence.effect_rank,EXCLUDED.effect_rank)`, [commandId, rank]);
        });
    }
    async cumulativeEffect(turnId) {
        return this.runtime.transaction(async (client) => {
            const row = (await client.query('SELECT max(e.effect_rank) AS effect FROM hr_runtime.commands c LEFT JOIN hr_runtime.recovery_evidence e USING(command_id) WHERE c.turn_id=$1', [turnId])).rows[0];
            return ['read_only', 'local_idempotent', 'external_side_effect'][row.effect ?? -1] ?? 'unknown';
        });
    }
    async markReconciliationRequired(commandId) {
        await this.runtime.transaction(async (client) => {
            if (!(await new V5CommandStore(this.runtime).lockCommand(client, commandId)))
                throw new Error('recovery binding invalid');
            await client.query(`INSERT INTO hr_runtime.recovery_evidence(command_id,reconciliation_required) VALUES ($1,true)
         ON CONFLICT(command_id) DO UPDATE SET reconciliation_required=true,evidence_complete=false`, [commandId]);
        });
    }
    /** M04a never grants. A safe local observation is not a Platform permit. */
    async requestReplayPermit(turnId) {
        return this.runtime.transaction(async (client) => {
            const rows = (await client.query(`SELECT c.terminal_kind,c.terminal_event_seq,e.* FROM hr_runtime.commands c LEFT JOIN hr_runtime.recovery_evidence e USING(command_id)
         WHERE c.turn_id=$1 ORDER BY c.attempt_no`, [turnId])).rows;
            const reason = rows.some((row) => row.terminal_kind === 'completed' && row.terminal_event_seq)
                ? 'result_available'
                : !rows.length || rows.some((row) => !row.stop_proof_ref)
                    ? 'execution_uncertain'
                    : rows.some((row) => !row.evidence_complete || row.has_output || row.effect_rank !== 0)
                        ? 'replay_not_safe'
                        : 'authority_unavailable';
            return { kind: 'denied', reason };
        });
    }
    async lock(client, binding) {
        const command = await new V5CommandStore(this.runtime).lockCommand(client, binding.commandId);
        if (!command)
            return undefined;
        const accepted = JSON.parse(command.accepted_payload);
        if (['turnId', 'principalRef', 'runId', 'attemptId', 'commandHash'].some((key) => accepted[key] !== binding[key]))
            return undefined;
        const intent = (await client.query('SELECT state,launch_lease_epoch FROM hr_runtime.execution_intents WHERE command_id=$1 FOR UPDATE', [binding.commandId])).rows[0];
        if (!intent || intent.state === 'pending' || Number(intent.launch_lease_epoch) !== binding.leaseEpoch)
            return undefined;
        return { ...command, intentState: intent.state };
    }
    async registerExecutor(binding, executorRef, identity) {
        if (!validIdentity(identity) || !executorRef || executorRef.length > 256)
            return false;
        return this.runtime.transaction(async (client) => {
            const command = await this.lock(client, binding);
            if (!command || command.terminal_kind || command.intentState !== 'claimed')
                return false;
            await client.query('INSERT INTO hr_runtime.recovery_evidence(command_id) VALUES ($1) ON CONFLICT DO NOTHING', [
                binding.commandId,
            ]);
            const evidence = (await client.query('SELECT stop_proof_ref FROM hr_runtime.recovery_evidence WHERE command_id=$1', [
                binding.commandId,
            ])).rows[0];
            if (evidence.stop_proof_ref)
                return false;
            const encoded = JSON.stringify(canonical(identity));
            await client.query('INSERT INTO hr_runtime.executor_ownership(command_id,executor_ref,identity_json) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING', [binding.commandId, executorRef, encoded]);
            const original = (await client.query('SELECT identity_json FROM hr_runtime.executor_ownership WHERE command_id=$1 AND executor_ref=$2', [binding.commandId, executorRef])).rows[0];
            return original?.identity_json === encoded;
        });
    }
    /** Trusted native exit observation, never called from a wire payload or resource-close result. */
    async recordNativeExit(binding, executorRef, identity) {
        if (!validIdentity(identity))
            return null;
        return this.runtime.transaction(async (client) => {
            if (!(await this.lock(client, binding)))
                return null;
            const observed = await client.query('UPDATE hr_runtime.executor_ownership SET exited_at=COALESCE(exited_at,now()) WHERE command_id=$1 AND executor_ref=$2 AND identity_json=$3 RETURNING executor_ref', [binding.commandId, executorRef, JSON.stringify(canonical(identity))]);
            if (!observed.rowCount)
                return null;
            const alive = await client.query('SELECT 1 FROM hr_runtime.executor_ownership WHERE command_id=$1 AND exited_at IS NULL', [binding.commandId]);
            if (alive.rowCount)
                return null;
            const proof = (await client.query('UPDATE hr_runtime.recovery_evidence SET stop_proof_ref=COALESCE(stop_proof_ref,$2),stopped_at=COALESCE(stopped_at,now()) WHERE command_id=$1 RETURNING stop_proof_ref', [binding.commandId, `executor-stop:${randomUUID()}`])).rows[0];
            return proof?.stop_proof_ref ?? null;
        });
    }
    async releaseStopped(binding, proofRef) {
        return this.runtime.transaction(async (client) => {
            const command = await this.lock(client, binding);
            if (!command?.terminal_event_seq)
                return 'denied';
            const evidence = (await client.query('SELECT stop_proof_ref FROM hr_runtime.recovery_evidence WHERE command_id=$1', [
                binding.commandId,
            ])).rows[0];
            if (!evidence?.stop_proof_ref || evidence.stop_proof_ref !== proofRef)
                return 'denied';
            if (command.intentState === 'ended')
                return 'duplicate';
            const released = await client.query('UPDATE hr_runtime.sessions SET active_command_id=NULL WHERE logical_session_id=$1 AND active_command_id=$2 RETURNING logical_session_id', [command.logical_session_id, binding.commandId]);
            if (!released.rowCount)
                return 'denied';
            await client.query("UPDATE hr_runtime.execution_intents SET state='ended' WHERE command_id=$1 AND state='claimed'", [binding.commandId]);
            return 'released';
        });
    }
}
export function validIdentity(identity) {
    return (!!identity &&
        ['darwin', 'linux'].includes(identity.platform) &&
        ['pty', 'process'].includes(identity.backend) &&
        typeof identity.hostId === 'string' &&
        identity.hostId.length > 0 &&
        identity.hostId.length <= 128 &&
        typeof identity.bootId === 'string' &&
        identity.bootId.length > 0 &&
        identity.bootId.length <= 128 &&
        typeof identity.startId === 'string' &&
        identity.startId.length > 0 &&
        identity.startId.length <= 128 &&
        Number.isSafeInteger(identity.pid) &&
        identity.pid > 0 &&
        Number.isSafeInteger(identity.uid) &&
        identity.uid >= 0);
}
//# sourceMappingURL=execution-recovery-ledger.js.map