import json
from datetime import datetime
from typing import TYPE_CHECKING, Callable, Iterator, Protocol, TypedDict, final
from urllib.parse import urljoin

import httpretty
import pytest
import requests
from mimesis.locales import Locale
from mimesis.schema import Field, Schema
from typing_extensions import TypeAlias, Unpack

from server.apps.identity.container import container
from server.apps.identity.intrastructure.services.placeholder import LeadCreate
from server.apps.identity.models import User
from server.common.django.types import Settings

if TYPE_CHECKING:
    from django.test import Client


UserAssertion: TypeAlias = Callable[[str, 'UserData'], None]


@pytest.fixture()
def settings() -> Settings:
    """Get Django settings."""
    return container.resolve(Settings)


class UserData(TypedDict, total=False):
    """Represent the simplified user data that is required to create a new user.

    It does not include ``password``, because it is very special in django.
    Importing this type is only allowed under ``if TYPE_CHECKING`` in tests.
    """

    email: str
    first_name: str
    last_name: str
    date_of_birth: datetime
    address: str
    job_title: str
    phone: str


@final
class RegistrationData(UserData, total=False):
    """Represent the registration data that is required to create a new user.

    Importing this type is only allowed under ``if TYPE_CHECKING`` in tests.
    """

    password1: str
    password2: str


@final
class APIUserResponse(TypedDict, total=False):
    """Represent the API response for a new user.

    Importing this type is only allowed under ``if TYPE_CHECKING`` in tests.
    """

    lead_id: int


@final
class LeadIDData(TypedDict, total=False):
    """Represent the lead_id with Placeholder API id.

    Importing this type is only allowed under ``if TYPE_CHECKING`` in tests.
    """

    lead_id: int


class RegistrationDataFactory(Protocol):
    """Annotations for RegistrationData factory."""

    def __call__(self, **fields: Unpack[RegistrationData]) -> RegistrationData:
        """User data factory protocol."""


class LeadIDDataFactory(Protocol):
    """Annotations for LeadIDData factory."""

    def __call__(self, api_mock: Callable[..., None]) -> LeadIDData:
        """User data factory protocol."""


@pytest.fixture()
def registration_data_factory(faker_seed: int) -> 'RegistrationDataFactory':
    """Returns factory for fake random data for registration."""

    def factory(**fields: Unpack['RegistrationData']) -> 'RegistrationData':
        mf = Field(locale=Locale.RU, seed=faker_seed)
        password = mf('password')  # by default passwords are equal
        schema = Schema(schema=lambda: {
            'email': mf('person.email'),
            'first_name': mf('person.first_name'),
            'last_name': mf('person.last_name'),
            'date_of_birth': mf('datetime.date'),
            'address': mf('address.city'),
            'job_title': mf('person.occupation'),
            'phone': mf('person.telephone'),
        })
        return {
            **schema.create(iterations=1)[0],  # type: ignore[misc]
            **{'password1': password, 'password2': password},
            **fields,
        }
    return factory


@pytest.fixture()
def registration_data(
    registration_data_factory: 'RegistrationDataFactory',
) -> 'RegistrationData':
    """Returns random registration data."""
    return registration_data_factory()


@pytest.fixture(scope='session')
def assert_correct_user() -> UserAssertion:
    """Assert correct user creation."""
    def factory(email: str, expected: UserData) -> None:
        user = User.objects.get(email=email)
        # Special fields:
        assert user.id
        assert user.is_active
        assert not user.is_superuser
        assert not user.is_staff
        # All other fields:
        for field_name, data_value in expected.items():
            assert getattr(user, field_name) == data_value
    return factory


@pytest.fixture()
def user_data(registration_data: 'RegistrationData') -> 'UserData':
    """We need to simplify registration data to drop passwords.

    Basically, it is the same as ``registration_data``, but without passwords.
    """
    return {  # type: ignore[return-value]
        key_name: value_part
        for key_name, value_part in registration_data.items()
        if not key_name.startswith('password')
    }


@pytest.fixture(scope='session')
def mimesis_field(faker_seed: int) -> Field:
    """Returns mimesis field with random seed."""
    return Field(locale=Locale.RU, seed=faker_seed)


@pytest.fixture()
def user(
    mimesis_field: Field,
    user_data: UserData,
    django_user_model: User,
) -> 'User':
    """Returns app user."""
    fields = dict(user_data)
    fields['password'] = mimesis_field('password')

    return django_user_model.objects.create(**fields)


@pytest.fixture()
def user_client(user: 'User', client: 'Client') -> 'Client':
    """Returns logged in client."""
    client.force_login(user)

    return client


@pytest.fixture()
def placeholder_api_user_response(
    faker_seed: int,
) -> APIUserResponse:
    """Create fake external api response for users."""
    mf = Field(locale=Locale.RU, seed=faker_seed)
    schema = Schema(schema=lambda: {
        'id': mf('numeric.increment'),
    })
    return schema.create(iterations=1)[0]  # type: ignore[return-value]


@pytest.fixture()
def placeholder_api_mock(
    settings: Settings,
    placeholder_api_user_response: APIUserResponse,
) -> Iterator[APIUserResponse]:
    """Mock `lead_id` generation."""
    with httpretty.httprettized():
        httpretty.register_uri(
            method=httpretty.POST,
            body=json.dumps(placeholder_api_user_response),
            uri=urljoin(settings.PLACEHOLDER_API_URL, LeadCreate._url_path),
        )
        yield placeholder_api_user_response
        assert httpretty.has_request()


@pytest.fixture()
def json_server_users() -> APIUserResponse:
    """Get lead_id from json_server."""
    return requests.get(
        'http://json_server/users',
        timeout=1,
    ).json()


@pytest.fixture()
def json_server_api_mock(
    settings: Settings,
    json_server_users: APIUserResponse,
) -> Iterator[APIUserResponse]:
    """Mock `lead_id` generation."""
    with httpretty.httprettized():
        httpretty.register_uri(
            method=httpretty.POST,
            body=json.dumps(json_server_users),
            uri=urljoin(settings.PLACEHOLDER_API_URL, LeadCreate._url_path),
        )
        yield json_server_users
        assert httpretty.has_request()


@pytest.fixture()
def real_request() -> APIUserResponse:
    """Get lead_id from httpbin.org."""
    return requests.post(
        'https://httpbin.org/anything',
        json={'id': 10},
        timeout=1,
    ).json()['json']


@pytest.fixture()
def real_request_mock(
    settings: Settings,
    real_request: APIUserResponse,
) -> Iterator[APIUserResponse]:
    """Mock `lead_id` generation."""
    with httpretty.httprettized():
        httpretty.register_uri(
            method=httpretty.POST,
            body=json.dumps(real_request),
            uri=urljoin(settings.PLACEHOLDER_API_URL, LeadCreate._url_path),
        )
        yield real_request
        assert httpretty.has_request()


@pytest.fixture()
def lead_id_mock_factory() -> LeadIDDataFactory:
    """Change key for valid using data in user creation."""
    def factory(api_mock: Callable[..., None]) -> LeadIDData:
        return {'lead_id': api_mock['id']}  # type: ignore[index]
    return factory
