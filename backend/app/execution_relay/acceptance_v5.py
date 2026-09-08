"""Trusted HTTP acceptance response validation; never executor-stop evidence."""

from dataclasses import dataclass

from .contracts_v5 import CoreChatCommandV5, V5ContractError


@dataclass(frozen=True)
class AcceptanceV5:
    command_id: str
    run_id: str
    command_hash: str
    launch_lease_epoch: int | None
    transport_lease_epoch: int
    intent_state: str


def parse_v5_acceptance(
    value: object,
    command: CoreChatCommandV5,
    *,
    previous: AcceptanceV5 | None = None,
) -> AcceptanceV5:
    """Use only on the authenticated local HTTP response, bound to what was sent."""
    try:
        expected = {
            "contractVersion": command.contract_version,
            "commandId": str(command.command_id),
            "runId": str(command.run_id),
            "commandSeq": command.command_seq,
            "status": "accepted",
        }
        if (
            type(value) is not dict
            or set(value) != {*expected, "duplicate", "executionState", "acceptance"}
            or any(value[key] != member for key, member in expected.items())
            or type(value["commandSeq"]) is not int
            or type(value["duplicate"]) is not bool
            or value["executionState"]
            not in {
                "accepted",
                "claimed",
                "reconciliation_required",
                "replay_permit_required",
            }
        ):
            raise ValueError
        acceptance = value["acceptance"]
        identity = {
            "version": "hr_dispatch_acceptance_v1",
            "principalRef": command.principal_ref,
            "turnId": str(command.turn_id),
            "attemptId": str(command.attempt_id),
            "commandHash": command.command_hash,
        }
        if (
            type(acceptance) is not dict
            or set(acceptance)
            != {*identity, "launchLeaseEpoch", "transportLeaseEpoch", "intentState"}
            or any(acceptance[key] != member for key, member in identity.items())
        ):
            raise ValueError
        launch = acceptance["launchLeaseEpoch"]
        transport = acceptance["transportLeaseEpoch"]
        state = acceptance["intentState"]
        if (
            type(transport) is not int
            or transport != command.lease_epoch
            or state not in {"pending", "claimed", "ended"}
            or (
                launch is not None
                and (type(launch) is not int or not 1 <= launch <= transport)
            )
            or (state == "claimed" and launch is None)
            or (state == "pending" and launch is not None)
            or (
                value["executionState"] in {"claimed", "reconciliation_required"}
                and state != "claimed"
            )
            or (
                value["executionState"] == "replay_permit_required"
                and state != "pending"
            )
        ):
            raise ValueError
        result = AcceptanceV5(
            value["commandId"],
            value["runId"],
            acceptance["commandHash"],
            launch,
            transport,
            state,
        )
        if previous is not None and (
            (previous.command_id, previous.run_id, previous.command_hash)
            != (result.command_id, result.run_id, result.command_hash)
            or previous.transport_lease_epoch > transport
            or (
                previous.launch_lease_epoch is not None
                and previous.launch_lease_epoch != launch
            )
        ):
            raise ValueError
        return result
    except (KeyError, TypeError, ValueError):
        raise V5ContractError("v5 acceptance invalid") from None
