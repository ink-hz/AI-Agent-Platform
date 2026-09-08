from copy import deepcopy

import pytest
from test_execution_contract_v5 import _v5_command

from app.execution_relay.acceptance_v5 import parse_v5_acceptance
from app.execution_relay.contracts_v5 import V5ContractError, parse_v5_command


def response(command, *, launch=1, transport=None, state="claimed"):
    return {
        "contractVersion": command.contract_version,
        "commandId": str(command.command_id),
        "runId": str(command.run_id),
        "commandSeq": command.command_seq,
        "status": "accepted",
        "duplicate": True,
        "executionState": "accepted",
        "acceptance": {
            "version": "hr_dispatch_acceptance_v1",
            "principalRef": command.principal_ref,
            "turnId": str(command.turn_id),
            "attemptId": str(command.attempt_id),
            "commandHash": command.command_hash,
            "launchLeaseEpoch": launch,
            "transportLeaseEpoch": transport or command.lease_epoch,
            "intentState": state,
        },
    }


def test_attestation_parses_actual_origin_separately_from_current_transport():
    value = _v5_command()
    value["leaseEpoch"] = 5
    command = parse_v5_command(value)
    accepted = parse_v5_acceptance(response(command, launch=2), command)
    assert accepted is not None
    assert accepted.launch_lease_epoch == 2
    assert accepted.transport_lease_epoch == 5


@pytest.mark.parametrize(
    "field,value",
    [
        ("principalRef", "wrong"),
        ("turnId", "wrong"),
        ("attemptId", "wrong"),
        ("commandHash", "0" * 64),
        ("version", "other"),
        ("launchLeaseEpoch", None),
        ("launchLeaseEpoch", True),
        ("launchLeaseEpoch", 0),
        ("launchLeaseEpoch", 6),
        ("transportLeaseEpoch", 4),
        ("transportLeaseEpoch", True),
        ("intentState", "stopped"),
        ("extra", "forbidden"),
    ],
)
def test_attestation_rejects_unbound_and_inconsistent_claims(field, value):
    raw = _v5_command()
    raw["leaseEpoch"] = 5
    command = parse_v5_command(raw)
    ack = response(command)
    ack["acceptance"][field] = value
    with pytest.raises(V5ContractError, match="^v5 acceptance invalid$"):
        parse_v5_acceptance(ack, command)


@pytest.mark.parametrize(
    "field,value",
    [
        ("commandId", "wrong"),
        ("runId", "wrong"),
        ("commandSeq", True),
        ("status", "completed"),
        ("duplicate", 1),
        ("executionState", "stopped"),
        ("contractVersion", "v4"),
        ("extra", "forbidden"),
    ],
)
def test_attestation_rejects_outer_response_mismatch(field, value):
    command = parse_v5_command(_v5_command())
    ack = response(command)
    ack[field] = value
    with pytest.raises(V5ContractError):
        parse_v5_acceptance(ack, command)


def test_pending_and_ended_are_not_a_launch_or_stop_proof_and_origin_never_changes():
    raw = _v5_command()
    raw["leaseEpoch"] = 5
    command = parse_v5_command(raw)
    for state in ("pending", "ended"):
        assert (
            parse_v5_acceptance(
                response(command, launch=None, state=state), command
            ).launch_lease_epoch
            is None
        )
    pending_with_launch = response(command, state="pending")
    with pytest.raises(V5ContractError):
        parse_v5_acceptance(pending_with_launch, command)
    previous = parse_v5_acceptance(response(command, launch=2), command)
    with pytest.raises(V5ContractError):
        parse_v5_acceptance(response(command, launch=3), command, previous=previous)
    older = deepcopy(raw)
    older["leaseEpoch"] = 4
    old_command = parse_v5_command(older)
    with pytest.raises(V5ContractError):
        parse_v5_acceptance(
            response(old_command, launch=2), old_command, previous=previous
        )
