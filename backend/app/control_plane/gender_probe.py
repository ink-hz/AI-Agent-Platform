from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import sys

from .dingtalk import (
    DINGTALK_GENDER_ATTRIBUTE,
    DingTalkClient,
    DingTalkDirectorySnapshotError,
    DingTalkMember,
    hydrate_authoritative_members,
    member_identity_snapshot,
)
from .directory_limits import (
    MAX_DEPARTMENTS,
    MAX_MEMBERS,
)
from .worker_runtime import load_worker_settings


_FAILED_MARKER = "DINGTALK_GENDER_PROBE_FAILED"


class GenderProbeError(RuntimeError):
    """A stable coverage-probe failure containing no provider material."""


@dataclass(frozen=True)
class GenderCoverage:
    active_employee_count: int
    valid_count: int
    missing_count: int
    invalid_count: int

    @property
    def permission_readable(self) -> bool:
        return self.valid_count + self.invalid_count > 0

    @property
    def ready(self) -> bool:
        return (
            self.active_employee_count > 0
            and self.valid_count == self.active_employee_count
        )

    def as_public_dict(self) -> dict[str, str | int | bool]:
        return {
            "attribute_name": DINGTALK_GENDER_ATTRIBUTE,
            "active_employee_count": self.active_employee_count,
            "valid_count": self.valid_count,
            "missing_count": self.missing_count,
            "invalid_count": self.invalid_count,
            "permission_readable": self.permission_readable,
            "ready": self.ready,
        }


async def collect_gender_coverage(client: DingTalkClient) -> GenderCoverage:
    """Collect one bounded, aggregate-only DingTalk gender coverage snapshot."""
    try:
        try:
            department_ids = [1]
            async for department in client.iter_departments():
                department_ids.append(department.department_id)
                if len(department_ids) > MAX_DEPARTMENTS:
                    raise GenderProbeError("department_count_bound")

            discovered: dict[str, DingTalkMember] = {}
            for department_id in department_ids:
                async for member in client.iter_department_members(department_id):
                    previous = discovered.get(member.userid)
                    if (
                        previous is not None
                        and member_identity_snapshot(previous)
                        != member_identity_snapshot(member)
                    ):
                        raise GenderProbeError("member_conflict")
                    discovered[member.userid] = member
                    if len(discovered) > MAX_MEMBERS:
                        raise GenderProbeError("member_count_bound")

            try:
                members = await hydrate_authoritative_members(client, discovered)
            except DingTalkDirectorySnapshotError:
                raise GenderProbeError("member_conflict") from None

            valid_count = 0
            missing_count = 0
            invalid_count = 0
            for member in members.values():
                if not member.active:
                    continue
                if member.gender_attribute_status == "valid":
                    valid_count += 1
                elif member.gender_attribute_status == "missing":
                    missing_count += 1
                else:
                    invalid_count += 1
            return GenderCoverage(
                active_employee_count=valid_count + missing_count + invalid_count,
                valid_count=valid_count,
                missing_count=missing_count,
                invalid_count=invalid_count,
            )
        finally:
            await client.aclose()
    except asyncio.CancelledError:
        raise
    except GenderProbeError:
        raise
    except Exception:
        raise GenderProbeError("provider_failed") from None


def main() -> int:
    try:
        settings = load_worker_settings("directory")
        client = DingTalkClient(
            app_key=settings.app_key,
            app_secret=settings.app_secret,
            corp_id=settings.corp_id,
            login_flow="in_client",
        )
        coverage = asyncio.run(collect_gender_coverage(client))
    except KeyboardInterrupt:
        raise
    except Exception:
        sys.stderr.write(f"{_FAILED_MARKER}\n")
        return 1

    sys.stdout.write(
        json.dumps(coverage.as_public_dict(), ensure_ascii=False, sort_keys=True)
        + "\n"
    )
    if coverage.ready and coverage.permission_readable:
        return 0
    sys.stderr.write(f"{_FAILED_MARKER}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
