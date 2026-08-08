"""Issue #589: every user attribute a claim mapper reads must be declared.

Keycloak's declarative user profile decides which attributes may be stored on a
user. Anything undeclared is silently dropped when unmanaged attributes are
disabled -- which is the default, and was this realm's state. The consequence was
not a broken realm import (import bypasses profile validation, so the five seeded
users looked fine) but a broken *provisioning* path: a user created through the
Admin API or the admin console got no `org`/`groups`, their token carried no such
claims, and FR-26's access-scope leg then matched only ALL_AUTHENTICATED. Fails
closed, so no leak -- but every real user is silently under-permissioned, and the
symptom (empty results, or a 403 on curating an org-scoped document) points at
authorization code rather than at realm configuration.

The required set is derived from the realm's own claim mappers rather than
hard-coded, so adding a mapper for a new attribute without declaring that
attribute fails here instead of in a deployment.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REALM_PATH = (
    Path(__file__).resolve().parents[2]
    / "infra"
    / "keycloak"
    / "realm-export"
    / "nexus-rag-realm.json"
)
REALM = json.loads(REALM_PATH.read_text(encoding="utf-8"))

USER_PROFILE_PROVIDER = "org.keycloak.userprofile.UserProfileProvider"
ATTRIBUTE_MAPPER = "oidc-usermodel-attribute-mapper"


def user_profile() -> dict:
    components = REALM.get("components", {})
    providers = components.get(USER_PROFILE_PROVIDER, [])
    assert providers, (
        "the realm declares no user-profile component, so Keycloak applies its "
        "default profile (username/email/firstName/lastName, unmanaged attributes "
        "disabled) and drops org/groups on any user provisioned after import (#589)"
    )
    raw = providers[0]["config"]["kc.user.profile.config"][0]
    return json.loads(raw)


def declared_attributes() -> set[str]:
    return {a["name"] for a in user_profile().get("attributes", [])}


def attributes_read_by_claim_mappers() -> dict[str, str]:
    """user attribute -> claim name, for every attribute-backed claim mapper."""
    wanted: dict[str, str] = {}
    for scope in REALM.get("clientScopes", []):
        for mapper in scope.get("protocolMappers", []):
            if mapper.get("protocolMapper") != ATTRIBUTE_MAPPER:
                continue
            config = mapper.get("config", {})
            attribute = config.get("user.attribute")
            if attribute:
                wanted[attribute] = config.get("claim.name", mapper.get("name", "?"))
    for client in REALM.get("clients", []):
        for mapper in client.get("protocolMappers", []):
            if mapper.get("protocolMapper") != ATTRIBUTE_MAPPER:
                continue
            config = mapper.get("config", {})
            attribute = config.get("user.attribute")
            if attribute:
                wanted[attribute] = config.get("claim.name", mapper.get("name", "?"))
    return wanted


def test_the_realm_has_claim_mappers_backed_by_user_attributes() -> None:
    """Guards the test below: an empty mapper set would make it vacuous."""
    mapped = attributes_read_by_claim_mappers()
    assert mapped, "no attribute-backed claim mappers found -- has the claims scope moved?"
    # The two FR-26 scoping claims must be among them; clearance/releasability are
    # role-derived and deliberately have no mappers of their own.
    assert {"org", "groups"} <= set(mapped)


@pytest.mark.parametrize("attribute", sorted(attributes_read_by_claim_mappers()))
def test_every_mapped_attribute_is_declared_in_the_user_profile(attribute: str) -> None:
    claim = attributes_read_by_claim_mappers()[attribute]
    assert attribute in declared_attributes(), (
        f"the realm maps user attribute {attribute!r} into the {claim!r} claim, but the "
        f"user profile does not declare it. Keycloak will silently drop it for any user "
        f"created through the Admin API or console (#589), so that claim will be absent "
        f"and FR-26 will under-permission the identity."
    )


def test_groups_is_multivalued_and_org_is_not() -> None:
    """`groups` is a list in the claims schema; `org` is a single value.

    A single-valued `groups` would truncate an identity to one need-to-know group,
    which fails closed but silently narrows access.
    """
    by_name = {a["name"]: a for a in user_profile()["attributes"]}
    assert by_name["groups"].get("multivalued") is True
    assert by_name["org"].get("multivalued") is False


def test_scoping_attributes_are_admin_editable_not_user_editable() -> None:
    """A user must not be able to edit their own access scope.

    `org`/`groups` feed FR-26 directly, so self-service editing would be privilege
    escalation through the account console rather than through the app.
    """
    by_name = {a["name"]: a for a in user_profile()["attributes"]}
    for attribute in ("org", "groups"):
        permissions = by_name[attribute].get("permissions", {})
        assert permissions.get("edit") == ["admin"], (
            f"{attribute} is editable by {permissions.get('edit')}; a user editing their "
            f"own org/groups would grant themselves access scope (FR-26)"
        )


def test_the_seeded_users_still_carry_the_mapped_attributes() -> None:
    """Import bypasses profile validation, but the fixture should not rely on that.

    If a seeded user stopped carrying `org`/`groups`, the dev stack's access-scope
    cases would silently stop exercising anything.
    """
    for user in REALM.get("users", []):
        attributes = user.get("attributes", {})
        assert attributes.get("org"), f"seeded user {user['username']} has no org attribute"
        assert "groups" in attributes, f"seeded user {user['username']} has no groups attribute"
