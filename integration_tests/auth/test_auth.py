from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from functools import partial
from typing import (
    ContextManager,
    DefaultDict,
    Dict,
    Generic,
    Iterator,
    Literal,
    Optional,
    Set,
    Tuple,
    TypeVar,
)

import jwt
import pytest
from faker import Faker
from httpx import HTTPStatusError
from opentelemetry.sdk.environment_variables import (
    OTEL_EXPORTER_OTLP_HEADERS,
    OTEL_EXPORTER_OTLP_TRACES_HEADERS,
)
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from phoenix.server.api.exceptions import Unauthorized
from phoenix.server.api.input_types.UserRoleInput import UserRoleInput
from strawberry.relay import GlobalID
from typing_extensions import assert_never

from .._helpers import (
    _ADMIN,
    _DEFAULT_ADMIN,
    _DENIED,
    _EXPECTATION_401,
    _EXPECTATION_404,
    _MEMBER,
    _OK,
    _OK_OR_DENIED,
    _SYSTEM_USER_GID,
    _AccessToken,
    _ApiKey,
    _create_user,
    _DefaultAdminTokenSequestration,
    _Expectation,
    _export_embeddings,
    _GetUser,
    _GqlId,
    _Headers,
    _log_in,
    _LoggedInUser,
    _Password,
    _patch_user,
    _patch_viewer,
    _Profile,
    _RefreshToken,
    _RoleOrUser,
    _SpanExporterFactory,
    _start_span,
    _Username,
)

NOW = datetime.now(timezone.utc)
_decode_jwt = partial(jwt.decode, options=dict(verify_signature=False))
_TokenT = TypeVar("_TokenT", _AccessToken, _RefreshToken)


class TestLogIn:
    @pytest.mark.parametrize("role_or_user", [_MEMBER, _ADMIN, _DEFAULT_ADMIN])
    def test_can_log_in(
        self,
        role_or_user: _RoleOrUser,
        _get_user: _GetUser,
    ) -> None:
        u = _get_user(role_or_user)
        u.log_in()

    @pytest.mark.parametrize("role_or_user", [_MEMBER, _ADMIN, _DEFAULT_ADMIN])
    def test_can_log_in_more_than_once_simultaneously(
        self,
        role_or_user: _RoleOrUser,
        _get_user: _GetUser,
    ) -> None:
        u = _get_user(role_or_user)
        for _ in range(10):
            u.log_in()

    @pytest.mark.parametrize("role_or_user", [_MEMBER, _ADMIN, _DEFAULT_ADMIN])
    def test_cannot_log_in_with_empty_password(
        self,
        role_or_user: _RoleOrUser,
        _get_user: _GetUser,
    ) -> None:
        u = _get_user(role_or_user)
        with _EXPECTATION_401:
            _log_in("", email=u.email)

    @pytest.mark.parametrize("role_or_user", [_MEMBER, _ADMIN, _DEFAULT_ADMIN])
    def test_cannot_log_in_with_wrong_password(
        self,
        role_or_user: _RoleOrUser,
        _get_user: _GetUser,
        _passwords: Iterator[_Password],
    ) -> None:
        u = _get_user(role_or_user)
        assert (wrong_password := next(_passwords)) != u.password
        with _EXPECTATION_401:
            _log_in(wrong_password, email=u.email)

    @pytest.mark.parametrize("role_or_user", [_MEMBER, _ADMIN])
    def test_cannot_log_in_with_deleted_user(
        self,
        role_or_user: _RoleOrUser,
        _get_user: _GetUser,
        _passwords: Iterator[_Password],
    ) -> None:
        admin_user = _get_user(UserRoleInput.ADMIN)
        user = _get_user(role_or_user)
        admin_user.delete_users(user)
        with _EXPECTATION_401:
            user.log_in()


class TestLogOut:
    def test_default_admin_cannot_log_out_during_testing(self) -> None:
        """
        This is not a functional test of Phoenix itself. Instead, it is intended to verify
        that the safeguard preventing the default admin from logging out during concurrent
        test runs is working as expected.
        """
        cls = _DefaultAdminTokenSequestration.exc_cls
        msg = _DefaultAdminTokenSequestration.message
        with pytest.raises(cls, match=msg):
            _DEFAULT_ADMIN.log_in().log_out()

    @pytest.mark.parametrize("role_or_user", [_MEMBER, _ADMIN])
    def test_can_log_out(
        self,
        role_or_user: _RoleOrUser,
        _get_user: _GetUser,
    ) -> None:
        u = _get_user(role_or_user)
        logged_in_users = [u.log_in() for _ in range(2)]
        for logged_in_user in logged_in_users:
            logged_in_user.create_api_key()
        logged_in_users[0].log_out()
        for logged_in_user in logged_in_users:
            with _EXPECTATION_401:
                logged_in_user.create_api_key()


class TestLoggedInTokens:
    class _JtiSet(Generic[_TokenT]):
        def __init__(self) -> None:
            self._set: Set[str] = set()

        def add(self, token: _TokenT) -> None:
            assert (jti := _decode_jwt(token)["jti"]) not in self._set
            self._set.add(jti)

    @pytest.mark.parametrize("role_or_user", [_MEMBER, _ADMIN])
    def test_logged_in_tokens_should_change_after_log_out(
        self,
        role_or_user: _RoleOrUser,
        _get_user: _GetUser,
    ) -> None:
        access_tokens = self._JtiSet[_AccessToken]()
        refresh_tokens = self._JtiSet[_RefreshToken]()
        u = _get_user(role_or_user)
        for _ in range(2):
            with u.log_in() as logged_in_user:
                access_tokens.add(logged_in_user.tokens.access_token)
                refresh_tokens.add(logged_in_user.tokens.refresh_token)

    @pytest.mark.parametrize("role_or_user", [_MEMBER, _ADMIN])
    @pytest.mark.parametrize("role", list(UserRoleInput))
    def test_logged_in_tokens_should_differ_between_users(
        self,
        role_or_user: _RoleOrUser,
        role: UserRoleInput,
        _get_user: _GetUser,
    ) -> None:
        access_tokens = self._JtiSet[_AccessToken]()
        refresh_tokens = self._JtiSet[_RefreshToken]()
        u = _get_user(role_or_user)
        with u.log_in() as logged_in_user:
            access_tokens.add(logged_in_user.tokens.access_token)
            refresh_tokens.add(logged_in_user.tokens.refresh_token)
        other_user = _get_user(role)
        with other_user.log_in() as logged_in_user:
            access_tokens.add(logged_in_user.tokens.access_token)
            refresh_tokens.add(logged_in_user.tokens.refresh_token)


class TestRefreshToken:
    @pytest.mark.parametrize("role_or_user", [_MEMBER, _ADMIN])
    def test_end_to_end_credentials_flow(
        self,
        role_or_user: _RoleOrUser,
        _get_user: _GetUser,
    ) -> None:
        u = _get_user(role_or_user)
        logged_in_users: DefaultDict[int, Dict[int, _LoggedInUser]] = defaultdict(dict)

        # user logs into first browser
        logged_in_users[0][0] = u.log_in()
        # user creates api key in the first browser
        logged_in_users[0][0].create_api_key()
        # tokens are refreshed in the first browser
        logged_in_users[0][1] = logged_in_users[0][0].refresh()
        # user creates api key in the first browser
        logged_in_users[0][1].create_api_key()
        # refresh token is good for one use only
        with pytest.raises(HTTPStatusError):
            logged_in_users[0][0].refresh()
        # original access token is invalid after refresh
        with _EXPECTATION_401:
            logged_in_users[0][0].create_api_key()

        # user logs into second browser
        logged_in_users[1][0] = u.log_in()
        # user creates api key in the second browser
        logged_in_users[1][0].create_api_key()

        # user logs out in first browser
        logged_in_users[0][1].log_out()
        # user is logged out of both browsers
        with _EXPECTATION_401:
            logged_in_users[0][1].create_api_key()
        with _EXPECTATION_401:
            logged_in_users[1][0].create_api_key()


class TestCreateUser:
    @pytest.mark.parametrize("role", list(UserRoleInput))
    def test_cannot_create_user_without_access(
        self,
        role: UserRoleInput,
        _profiles: Iterator[_Profile],
    ) -> None:
        profile = next(_profiles)
        with _EXPECTATION_401:
            _create_user(role=role, profile=profile)

    @pytest.mark.parametrize(
        "role_or_user,expectation",
        [
            (_MEMBER, _DENIED),
            (_ADMIN, _OK),
            (_DEFAULT_ADMIN, _OK),
        ],
    )
    @pytest.mark.parametrize("role", list(UserRoleInput))
    def test_only_admin_can_create_user(
        self,
        role_or_user: UserRoleInput,
        role: UserRoleInput,
        expectation: ContextManager[Optional[Unauthorized]],
        _get_user: _GetUser,
        _profiles: Iterator[_Profile],
    ) -> None:
        u = _get_user(role_or_user)
        logged_in_user = u.log_in()
        profile = next(_profiles)
        with expectation as e:
            new_user = logged_in_user.create_user(role, profile=profile)
        if not e:
            new_user.log_in()

    @pytest.mark.parametrize("role_or_user", [_ADMIN, _DEFAULT_ADMIN])
    @pytest.mark.parametrize("role", list(UserRoleInput))
    def test_username_is_optional(
        self,
        role_or_user: _RoleOrUser,
        role: UserRoleInput,
        _get_user: _GetUser,
        _profiles: Iterator[_Profile],
    ) -> None:
        profile = replace(next(_profiles), username=None)
        _get_user(role_or_user).create_user(role, profile=profile)


class TestPatchViewer:
    @pytest.mark.parametrize("role_or_user", [_MEMBER, _ADMIN, _DEFAULT_ADMIN])
    def test_cannot_patch_viewer_without_access(
        self,
        role_or_user: _RoleOrUser,
        _get_user: _GetUser,
    ) -> None:
        u = _get_user(role_or_user)
        with _EXPECTATION_401:
            _patch_viewer(None, u.password, new_username="new_username")

    @pytest.mark.parametrize("role_or_user", [_MEMBER, _ADMIN, _DEFAULT_ADMIN])
    def test_cannot_change_password_without_current_password(
        self,
        role_or_user: _RoleOrUser,
        _get_user: _GetUser,
        _passwords: Iterator[_Password],
    ) -> None:
        u = _get_user(role_or_user)
        logged_in_user = u.log_in()
        new_password = next(_passwords)
        with pytest.raises(Exception):
            _patch_viewer(logged_in_user, None, new_password=new_password)

    @pytest.mark.parametrize("role_or_user", [_MEMBER, _ADMIN, _DEFAULT_ADMIN])
    def test_cannot_change_password_with_wrong_current_password(
        self,
        role_or_user: _RoleOrUser,
        _get_user: _GetUser,
        _passwords: Iterator[_Password],
    ) -> None:
        u = _get_user(role_or_user)
        logged_in_user = u.log_in()
        assert (wrong_password := next(_passwords)) != logged_in_user.password
        new_password = next(_passwords)
        with pytest.raises(Exception):
            _patch_viewer(logged_in_user, wrong_password, new_password=new_password)

    @pytest.mark.parametrize("role", list(UserRoleInput))
    def test_change_password(
        self,
        role: UserRoleInput,
        _get_user: _GetUser,
        _passwords: Iterator[_Password],
    ) -> None:
        u = _get_user(role)
        logged_in_user = u.log_in()
        new_password = f"new_password_{next(_passwords)}"
        assert new_password != logged_in_user.password
        _patch_viewer(
            (old_token := logged_in_user.tokens),
            (old_password := logged_in_user.password),
            new_password=new_password,
        )
        another_password = f"another_password_{next(_passwords)}"
        with _EXPECTATION_401:
            _patch_viewer(old_token, new_password, new_password=another_password)
        with _EXPECTATION_401:
            _log_in(old_password, email=logged_in_user.email)
        new_tokens = _log_in(new_password, email=logged_in_user.email)
        with pytest.raises(Exception):
            _patch_viewer(new_tokens, old_password, new_password=another_password)

    @pytest.mark.parametrize("role", list(UserRoleInput))
    def test_change_username(
        self,
        role: UserRoleInput,
        _get_user: _GetUser,
        _usernames: Iterator[_Username],
        _passwords: Iterator[_Password],
    ) -> None:
        u = _get_user(role)
        logged_in_user = u.log_in()
        new_username = f"new_username_{next(_usernames)}"
        _patch_viewer(logged_in_user, None, new_username=new_username)
        another_username = f"another_username_{next(_usernames)}"
        wrong_password = next(_passwords)
        assert wrong_password != logged_in_user.password
        _patch_viewer(logged_in_user, wrong_password, new_username=another_username)


class TestPatchUser:
    @pytest.mark.parametrize("role_or_user", [_ADMIN, _DEFAULT_ADMIN])
    @pytest.mark.parametrize("new_role", list(UserRoleInput))
    def test_cannot_change_role_of_default_admin(
        self,
        role_or_user: _RoleOrUser,
        new_role: UserRoleInput,
        _get_user: _GetUser,
    ) -> None:
        u = _get_user(role_or_user)
        logged_in_user = u.log_in()
        with pytest.raises(Exception, match="role"):
            logged_in_user.patch_user(_DEFAULT_ADMIN, new_role=new_role)

    @pytest.mark.parametrize(
        "role_or_user,expectation",
        [
            (_MEMBER, _DENIED),
            (_ADMIN, _OK),
            (_DEFAULT_ADMIN, _OK),
        ],
    )
    @pytest.mark.parametrize("role", list(UserRoleInput))
    @pytest.mark.parametrize("new_role", list(UserRoleInput))
    def test_only_admin_can_change_role_for_non_self(
        self,
        role_or_user: _RoleOrUser,
        role: UserRoleInput,
        new_role: UserRoleInput,
        expectation: ContextManager[Optional[Unauthorized]],
        _get_user: _GetUser,
    ) -> None:
        u = _get_user(role_or_user)
        logged_in_user = u.log_in()
        non_self = _get_user(role)
        assert non_self.gid != logged_in_user.gid
        with _EXPECTATION_401:
            _patch_user(non_self, new_role=new_role)
        with expectation:
            logged_in_user.patch_user(non_self, new_role=new_role)

    @pytest.mark.parametrize(
        "role_or_user,expectation",
        [
            (_MEMBER, _DENIED),
            (_ADMIN, _OK),
            (_DEFAULT_ADMIN, _OK),
        ],
    )
    @pytest.mark.parametrize("role", list(UserRoleInput))
    def test_only_admin_can_change_password_for_non_self(
        self,
        role_or_user: UserRoleInput,
        role: UserRoleInput,
        expectation: ContextManager[Optional[Unauthorized]],
        _get_user: _GetUser,
        _passwords: Iterator[_Password],
    ) -> None:
        u = _get_user(role_or_user)
        logged_in_user = u.log_in()
        non_self = _get_user(role)
        assert non_self.gid != logged_in_user.gid
        old_password = non_self.password
        new_password = f"new_password_{next(_passwords)}"
        assert new_password != old_password
        with _EXPECTATION_401:
            _patch_user(non_self, new_password=new_password)
        with expectation as e:
            logged_in_user.patch_user(non_self, new_password=new_password)
        if e:
            non_self.log_in()
            return
        email = non_self.email
        with _EXPECTATION_401:
            _log_in(old_password, email=email)
        _log_in(new_password, email=email)

    @pytest.mark.parametrize(
        "role_or_user,expectation",
        [
            (_MEMBER, _DENIED),
            (_ADMIN, _OK),
            (_DEFAULT_ADMIN, _OK),
        ],
    )
    @pytest.mark.parametrize("role", list(UserRoleInput))
    def test_only_admin_can_change_username_for_non_self(
        self,
        role_or_user: _RoleOrUser,
        role: UserRoleInput,
        expectation: ContextManager[Optional[Unauthorized]],
        _get_user: _GetUser,
        _usernames: Iterator[_Username],
    ) -> None:
        u = _get_user(role_or_user)
        logged_in_user = u.log_in()
        non_self = _get_user(role)
        assert non_self.gid != logged_in_user.gid
        old_username = non_self.username
        new_username = f"new_username_{next(_usernames)}"
        assert new_username != old_username
        with _EXPECTATION_401:
            _patch_user(non_self, new_username=new_username)
        with expectation:
            logged_in_user.patch_user(non_self, new_username=new_username)


class TestDeleteUsers:
    @pytest.mark.parametrize(
        "role_or_user,expectation",
        [
            (_MEMBER, _DENIED),
            (_ADMIN, pytest.raises(Exception)),
            (_DEFAULT_ADMIN, pytest.raises(Exception)),
        ],
    )
    def test_cannot_delete_system_user(
        self,
        role_or_user: UserRoleInput,
        expectation: _Expectation,
        _get_user: _GetUser,
    ) -> None:
        u = _get_user(role_or_user)
        logged_in_user = u.log_in()
        with expectation:
            logged_in_user.delete_users(_SYSTEM_USER_GID)

    @pytest.mark.parametrize(
        "role_or_user,expectation",
        [
            (_MEMBER, _DENIED),
            (_ADMIN, pytest.raises(Exception, match="Cannot delete the default admin user")),
            (_DEFAULT_ADMIN, pytest.raises(Exception)),
        ],
    )
    def test_cannot_delete_default_admin_user(
        self,
        role_or_user: _RoleOrUser,
        expectation: _Expectation,
        _get_user: _GetUser,
    ) -> None:
        u = _get_user(role_or_user)
        logged_in_user = u.log_in()
        with expectation:
            logged_in_user.delete_users(_DEFAULT_ADMIN)
        _DEFAULT_ADMIN.log_in()

    @pytest.mark.parametrize(
        "role_or_user,expectation",
        [
            (_MEMBER, _DENIED),
            (_ADMIN, _OK),
            (_DEFAULT_ADMIN, _OK),
        ],
    )
    @pytest.mark.parametrize("role", list(UserRoleInput))
    def test_only_admin_can_delete_users(
        self,
        role_or_user: _RoleOrUser,
        role: UserRoleInput,
        expectation: _OK_OR_DENIED,
        _get_user: _GetUser,
    ) -> None:
        u = _get_user(role_or_user)
        logged_in_user = u.log_in()
        non_self = _get_user(role)
        with expectation as e:
            logged_in_user.delete_users(non_self)
        if e:
            non_self.log_in()
        else:
            with _EXPECTATION_401:
                non_self.log_in()

    @pytest.mark.parametrize("role_or_user", [_ADMIN, _DEFAULT_ADMIN])
    def test_error_is_raised_when_deleting_a_non_existent_user_id(
        self,
        role_or_user: _RoleOrUser,
        _get_user: _GetUser,
    ) -> None:
        u = _get_user(role_or_user)
        logged_in_user = u.log_in()
        phantom = _GqlId(GlobalID(type_name="User", node_id=str(999_999_999)))
        user = _get_user()
        with pytest.raises(Exception, match="Some user IDs could not be found"):
            logged_in_user.delete_users(phantom, user)
        user.log_in()


class TestCreateApiKey:
    @pytest.mark.parametrize("role_or_user", [_MEMBER, _ADMIN, _DEFAULT_ADMIN])
    def test_create_user_api_key(
        self,
        role_or_user: _RoleOrUser,
        _get_user: _GetUser,
    ) -> None:
        u = _get_user(role_or_user)
        logged_in_user = u.log_in()
        logged_in_user.create_api_key()

    @pytest.mark.parametrize(
        "role_or_user,expectation",
        [
            (_MEMBER, _DENIED),
            (_ADMIN, _OK),
            (_DEFAULT_ADMIN, _OK),
        ],
    )
    def test_only_admin_can_create_system_api_key(
        self,
        role_or_user: _RoleOrUser,
        expectation: _OK_OR_DENIED,
        _get_user: _GetUser,
    ) -> None:
        u = _get_user(role_or_user)
        logged_in_user = u.log_in()
        with expectation:
            logged_in_user.create_api_key("System")


class TestDeleteApiKey:
    @pytest.mark.parametrize("role_or_user", [_MEMBER, _ADMIN, _DEFAULT_ADMIN])
    def test_delete_user_api_key(
        self,
        role_or_user: _RoleOrUser,
        _get_user: _GetUser,
    ) -> None:
        u = _get_user(role_or_user)
        logged_in_user = u.log_in()
        api_key = logged_in_user.create_api_key()
        logged_in_user.delete_api_key(api_key)

    @pytest.mark.parametrize(
        "role_or_user,expectation",
        [
            (_MEMBER, _DENIED),
            (_ADMIN, _OK),
            (_DEFAULT_ADMIN, _OK),
        ],
    )
    @pytest.mark.parametrize("role", list(UserRoleInput))
    def test_only_admin_can_delete_user_api_key_for_non_self(
        self,
        role_or_user: _RoleOrUser,
        role: UserRoleInput,
        expectation: _OK_OR_DENIED,
        _get_user: _GetUser,
    ) -> None:
        u = _get_user(role_or_user)
        logged_in_user = u.log_in()
        non_self = _get_user(role).log_in()
        assert non_self.gid != logged_in_user.gid
        api_key = non_self.create_api_key()
        with expectation:
            logged_in_user.delete_api_key(api_key)

    @pytest.mark.parametrize(
        "role_or_user,expectation",
        [
            (_MEMBER, _DENIED),
            (_ADMIN, _OK),
            (_DEFAULT_ADMIN, _OK),
        ],
    )
    def test_only_admin_can_delete_system_api_key(
        self,
        role_or_user: _RoleOrUser,
        expectation: _OK_OR_DENIED,
        _get_user: _GetUser,
    ) -> None:
        u = _get_user(role_or_user)
        logged_in_user = u.log_in()
        api_key = _DEFAULT_ADMIN.create_api_key("System")
        with expectation:
            logged_in_user.delete_api_key(api_key)


class TestGraphQLQuery:
    @pytest.mark.parametrize(
        "role_or_user,expectation",
        [
            (_MEMBER, _DENIED),
            (_ADMIN, _OK),
            (_DEFAULT_ADMIN, _OK),
        ],
    )
    @pytest.mark.parametrize(
        "query",
        [
            "query{users{edges{node{id}}}}",
            "query{userApiKeys{id}}",
            "query{systemApiKeys{id}}",
        ],
    )
    def test_only_admin_can_list_users_and_api_keys(
        self,
        role_or_user: _RoleOrUser,
        query: str,
        expectation: _OK_OR_DENIED,
        _get_user: _GetUser,
    ) -> None:
        u = _get_user(role_or_user)
        logged_in_user = u.log_in()
        with expectation:
            logged_in_user.gql(query)

    @pytest.mark.parametrize("role_or_user", [_MEMBER, _ADMIN, _DEFAULT_ADMIN])
    def test_can_query_user_node_for_self(
        self,
        role_or_user: _RoleOrUser,
        _get_user: _GetUser,
    ) -> None:
        u = _get_user(role_or_user)
        logged_in_user = u.log_in()
        query = 'query{node(id:"' + u.gid + '"){__typename}}'
        logged_in_user.gql(query)

    @pytest.mark.parametrize(
        "role_or_user,expectation",
        [
            (_MEMBER, _DENIED),
            (_ADMIN, _OK),
            (_DEFAULT_ADMIN, _OK),
        ],
    )
    @pytest.mark.parametrize("role", list(UserRoleInput))
    def test_only_admin_can_query_user_node_for_non_self(
        self,
        role_or_user: _RoleOrUser,
        role: UserRoleInput,
        expectation: _OK_OR_DENIED,
        _get_user: _GetUser,
    ) -> None:
        u = _get_user(role_or_user)
        logged_in_user = u.log_in()
        non_self = _get_user(role)
        query = 'query{node(id:"' + non_self.gid + '"){__typename}}'
        with expectation:
            logged_in_user.gql(query)


class TestSpanExporters:
    @pytest.fixture
    def _spans(self, _fake: Faker) -> Tuple[ReadableSpan, ...]:
        memory = InMemorySpanExporter()
        project_name, span_name = _fake.unique.pystr(), _fake.unique.pystr()
        _start_span(project_name=project_name, span_name=span_name, exporter=memory).end()
        return memory.get_finished_spans()

    @pytest.mark.parametrize(
        "use_api_key,expires_at,expected",
        [
            (True, NOW + timedelta(days=1), SpanExportResult.SUCCESS),
            (True, None, SpanExportResult.SUCCESS),
            (True, NOW, SpanExportResult.FAILURE),
            (False, None, SpanExportResult.FAILURE),
        ],
    )
    @pytest.mark.parametrize("method", ["headers", "setenv"])
    def test_api_key(
        self,
        method: Literal["headers", "setenv"],
        use_api_key: bool,
        expires_at: Optional[datetime],
        expected: SpanExportResult,
        _span_exporter: _SpanExporterFactory,
        _spans: Tuple[ReadableSpan, ...],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv(OTEL_EXPORTER_OTLP_HEADERS, False)
        monkeypatch.delenv(OTEL_EXPORTER_OTLP_TRACES_HEADERS, False)
        headers: Optional[_Headers] = None
        api_key: Optional[_ApiKey] = None
        if use_api_key:
            api_key = _DEFAULT_ADMIN.create_api_key("System", expires_at=expires_at)
            if method == "headers":
                # Must use all lower case for `authorization` because
                # otherwise it would crash the gRPC receiver.
                headers = dict(authorization=f"Bearer {api_key}")
            elif method == "setenv":
                monkeypatch.setenv(
                    OTEL_EXPORTER_OTLP_TRACES_HEADERS,
                    f"Authorization=Bearer {api_key}",
                )
            else:
                assert_never(method)
        export = _span_exporter(headers=headers).export
        for _ in range(2):
            assert export(_spans) is expected
        if api_key and expected is SpanExportResult.SUCCESS:
            _DEFAULT_ADMIN.delete_api_key(api_key)
            assert export(_spans) is SpanExportResult.FAILURE


class TestEmbeddingsRestApi:
    @pytest.mark.parametrize("role_or_user", [_MEMBER, _ADMIN, _DEFAULT_ADMIN])
    def test_authenticated_users_can_access_route(
        self,
        role_or_user: _RoleOrUser,
        _get_user: _GetUser,
    ) -> None:
        user = _get_user(role_or_user)
        logged_in_user = user.log_in()
        with _EXPECTATION_404:  # no files have been exported
            logged_in_user.export_embeddings("embeddings")

    def test_unauthenticated_requests_receive_401(self) -> None:
        with _EXPECTATION_401:
            _export_embeddings(None, filename="embeddings")