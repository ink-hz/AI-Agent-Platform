from __future__ import annotations

from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.control_plane.crypto import IdentityKeyring, ProviderIdentityCodec
from app.control_plane.middleware import is_office_recipient_directory_request
from app.control_plane.office_recipients import (
    OfficeDirectoryIssue,
    OfficeDirectoryMember,
    OfficeDirectoryPage,
    OfficeRecipientDirectoryError,
    OfficeRecipientDirectoryRepository,
    build_office_recipient_router,
    corporate_userid,
)

GENERATION_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
MEMBER_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
USER_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
AUTH = {"Authorization": f"Bearer {'s' * 32}"}


class FakeOfficeRecipientDirectoryService:
    def search(self, **_values) -> OfficeDirectoryPage:
        return OfficeDirectoryPage(
            directory_generation_id=GENERATION_ID,
            members=(
                OfficeDirectoryMember(
                    directory_member_id=MEMBER_ID,
                    internal_user_id=USER_ID,
                    display_name="苍渊",
                    real_name="苍渊",
                    departments=("AI Lab",),
                    status="active",
                    dingtalk_user_id="staff-001",
                ),
            ),
            next_cursor=None,
        )

    def resolve(self, **_values) -> OfficeDirectoryPage:
        return OfficeDirectoryPage(
            directory_generation_id=GENERATION_ID,
            members=(
                OfficeDirectoryMember(
                    directory_member_id=MEMBER_ID,
                    internal_user_id=USER_ID,
                    display_name="苍渊",
                    real_name="苍渊",
                    departments=("AI Lab",),
                    status="active",
                    dingtalk_user_id="staff-001",
                ),
            ),
            next_cursor=None,
        )

    def departments(self):
        return (
            {
                "department_id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                "parent_department_id": None,
                "display_name": "AI Lab",
            },
        )


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(
        build_office_recipient_router(
            FakeOfficeRecipientDirectoryService(), bearer_secret="s" * 32
        )
    )
    return TestClient(app, client=("127.0.0.1", 51000))


def test_internal_recipient_directory_is_loopback_bearer_bound_and_minimal():
    service = FakeOfficeRecipientDirectoryService()
    app = FastAPI()
    app.include_router(build_office_recipient_router(service, bearer_secret="s" * 32))

    public = TestClient(app, client=("203.0.113.7", 51000)).post(
        "/api/v1/internal/office/recipient-directory/resolve",
        headers=AUTH,
        json={"directory_member_ids": [str(MEMBER_ID)]},
    )
    missing = TestClient(app, client=("127.0.0.1", 51000)).post(
        "/api/v1/internal/office/recipient-directory/resolve",
        json={"directory_member_ids": [str(MEMBER_ID)]},
    )
    ok = TestClient(app, client=("127.0.0.1", 51000)).post(
        "/api/v1/internal/office/recipient-directory/resolve",
        headers=AUTH,
        json={"directory_member_ids": [str(MEMBER_ID)]},
    )

    assert public.status_code == 404
    assert missing.status_code == 404
    assert ok.status_code == 200
    assert ok.headers["cache-control"] == "no-store"
    assert ok.json() == {
        "directory_generation_id": str(GENERATION_ID),
        "members": [
            {
                "directory_member_id": str(MEMBER_ID),
                "internal_user_id": str(USER_ID),
                "dingtalk_user_id": "staff-001",
                "status": "active",
            }
        ],
        "unresolved": [],
    }
    assert "display_name" not in ok.text


def test_search_never_returns_dingtalk_recipient_id_to_directory_browser_adapter(
    client: TestClient,
):
    response = client.post(
        "/api/v1/internal/office/recipient-directory/search",
        headers=AUTH,
        json={"query": "苍渊", "limit": 20, "cursor": None},
    )

    assert response.status_code == 200
    assert set(response.json()["members"][0]) == {
        "directory_member_id",
        "internal_user_id",
        "display_name",
        "real_name",
        "departments",
        "status",
    }
    assert "dingtalk_user_id" not in response.text


def test_private_directory_rejects_extra_fields_and_bounds_arrays(
    client: TestClient,
):
    extra = client.post(
        "/api/v1/internal/office/recipient-directory/search",
        headers=AUTH,
        json={"query": "", "extra": True},
    )
    oversized = client.post(
        "/api/v1/internal/office/recipient-directory/resolve",
        headers=AUTH,
        json={"directory_member_ids": [str(MEMBER_ID)] * 201},
    )

    assert extra.status_code == 422
    assert oversized.status_code == 422
    assert extra.headers["cache-control"] == "no-store"
    assert oversized.headers["cache-control"] == "no-store"


def test_departments_never_returns_provider_identity(client: TestClient):
    response = client.get(
        "/api/v1/internal/office/recipient-directory/departments",
        headers=AUTH,
    )

    assert response.status_code == 200
    assert response.json() == {
        "departments": [
            {
                "department_id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                "parent_department_id": None,
                "display_name": "AI Lab",
            }
        ]
    }
    assert "dingtalk" not in response.text.lower()


def test_corporate_userid_accepts_only_the_exact_corporate_encoding():
    assert corporate_userid("ding-corp", "9:ding-corpstaff-001") == "staff-001"

    for invalid in (
        "ding-corpstaff-001",
        "x:ding-corpstaff-001",
        "8:ding-corpstaff-001",
        "9:otherstaff-001",
        "9:ding-corp",
        "9:ding-corpbad user",
    ):
        with pytest.raises(
            OfficeRecipientDirectoryError, match="provider_identity_invalid"
        ):
            corporate_userid("ding-corp", invalid)


def test_unresolved_resolution_serializes_explicit_reason():
    class UnresolvedService(FakeOfficeRecipientDirectoryService):
        def resolve(self, **_values) -> OfficeDirectoryPage:
            return OfficeDirectoryPage(
                directory_generation_id=GENERATION_ID,
                members=(),
                next_cursor=None,
                unresolved=(OfficeDirectoryIssue(MEMBER_ID, "inactive"),),
            )

    app = FastAPI()
    app.include_router(
        build_office_recipient_router(UnresolvedService(), bearer_secret="s" * 32)
    )
    response = TestClient(app, client=("127.0.0.1", 51000)).post(
        "/api/v1/internal/office/recipient-directory/resolve",
        headers=AUTH,
        json={"directory_member_ids": [str(MEMBER_ID)]},
    )

    assert response.json()["unresolved"] == [
        {"requested_id": str(MEMBER_ID), "reason": "inactive"}
    ]


def test_identity_middleware_recognizes_only_the_three_office_backchannels():
    base = "/api/v1/internal/office/recipient-directory"

    assert is_office_recipient_directory_request("POST", f"{base}/search")
    assert is_office_recipient_directory_request("POST", f"{base}/resolve")
    assert is_office_recipient_directory_request("GET", f"{base}/departments")
    assert not is_office_recipient_directory_request("GET", f"{base}/search")
    assert not is_office_recipient_directory_request("POST", f"{base}/departments")
    assert not is_office_recipient_directory_request("POST", f"{base}/search/extra")


def test_resolve_ignores_corrupt_optional_real_name_ciphertext():
    codec = ProviderIdentityCodec(
        IdentityKeyring(1, "provider-encryption", {1: b"e" * 32}),
        IdentityKeyring(
            1,
            "provider-lookup-hmac",
            {1: b"h" * 32},
            transition_versions=(1,),
        ),
    )
    protected = codec.seal("employee", "9:ding-corpstaff-001")

    class Repository(OfficeRecipientDirectoryRepository):
        def __init__(self):
            self._identity_codec = codec
            self._corp_id = "ding-corp"

        def _read(self, *_args, **_kwargs):
            return [
                {
                    "directory_generation_id": GENERATION_ID,
                    "row_kind": "member",
                    "directory_member_id": MEMBER_ID,
                    "internal_user_id": USER_ID,
                    "display_name": "苍渊",
                    "status": "active",
                    "encrypted_provider_id": protected.ciphertext,
                    "encryption_key_version": protected.encryption_key_version,
                    "real_name_ciphertext": b"invalid-ciphertext",
                    "real_name_nonce": b"n" * 12,
                    "real_name_encryption_key_version": 1,
                    "departments": ["AI Lab"],
                    "requested_id": MEMBER_ID,
                }
            ]

    page = Repository().resolve(
        directory_member_ids=(MEMBER_ID,),
        internal_user_ids=(),
    )

    assert [(member.directory_member_id, member.dingtalk_user_id) for member in page.members] == [
        (MEMBER_ID, "staff-001")
    ]
    assert page.unresolved == ()
