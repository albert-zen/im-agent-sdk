from __future__ import annotations

import asyncio
import importlib.util
import inspect
import unittest
from typing import cast, get_type_hints

import imagent.adapters as adapter_facade
import imagent.contracts as contract_facade
import imagent.gateway as gateway_facade
import imagent.gateway.delivery as delivery_facade
import imagent.gateway.persistence as historical_delivery_contracts
from imagent.gateway.delivery import proactive_authorization as authorization_owner


class ProactiveAuthorizationOwnershipTests(unittest.TestCase):
    def test_facades_use_exact_owner_objects_and_old_symbols_are_absent(self) -> None:
        self.assertEqual(
            authorization_owner.DeliveryAuthorizer.__module__,
            "imagent.gateway.delivery.proactive_authorization",
        )
        self.assertEqual(
            authorization_owner.DeliveryPrincipal.__module__,
            "imagent.gateway.delivery.proactive_authorization",
        )
        self.assertEqual(
            authorization_owner.validate_delivery_principal.__module__,
            "imagent.gateway.delivery.proactive_authorization",
        )
        self.assertIs(
            delivery_facade.DeliveryAuthorizer,
            authorization_owner.DeliveryAuthorizer,
        )
        self.assertIs(gateway_facade.DeliveryAuthorizer, authorization_owner.DeliveryAuthorizer)
        self.assertIs(adapter_facade.DeliveryAuthorizer, authorization_owner.DeliveryAuthorizer)
        self.assertIs(
            delivery_facade.DeliveryPrincipal,
            authorization_owner.DeliveryPrincipal,
        )
        self.assertIs(contract_facade.DeliveryPrincipal, authorization_owner.DeliveryPrincipal)
        self.assertIs(
            delivery_facade.validate_delivery_principal,
            authorization_owner.validate_delivery_principal,
        )
        self.assertIs(
            contract_facade.validate_delivery_principal,
            authorization_owner.validate_delivery_principal,
        )
        self.assertIs(
            delivery_facade.ScopedDeliveryAuthorizer,
            authorization_owner.ScopedDeliveryAuthorizer,
        )
        self.assertFalse(hasattr(historical_delivery_contracts, "DeliveryPrincipal"))
        self.assertFalse(hasattr(historical_delivery_contracts, "validate_delivery_principal"))
        self.assertIsNone(importlib.util.find_spec("imagent.proactive_delivery"))

    def test_public_principal_annotations_resolve_at_runtime(self) -> None:
        hints = get_type_hints(authorization_owner.DeliveryPrincipal)

        self.assertEqual(hints["allowed_threads"], tuple[contract_facade.ThreadRef, ...])
        self.assertEqual(
            hints["allowed_conversations"],
            tuple[contract_facade.ConversationRef, ...],
        )


class ScopedDeliveryAuthorizerTests(unittest.IsolatedAsyncioTestCase):
    def test_capacity_constructor_is_keyword_only_positive_non_bool_integer(self) -> None:
        parameter = inspect.signature(authorization_owner.ScopedDeliveryAuthorizer).parameters[
            "max_principals"
        ]
        self.assertIs(parameter.kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertEqual(parameter.default, 4096)
        for invalid in (0, -1, True, cast(int, 1.5), cast(int, "1")):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    authorization_owner.ScopedDeliveryAuthorizer(max_principals=invalid)

    async def test_issue_authenticate_revoke_and_generated_credential(self) -> None:
        authorizer = authorization_owner.ScopedDeliveryAuthorizer()
        principal = authorization_owner.DeliveryPrincipal("principal-a")

        generated = await authorizer.issue(principal, credential="")
        self.assertTrue(generated)
        self.assertEqual(await authorizer.authenticate(generated), principal)
        self.assertTrue(await authorizer.revoke(generated))
        self.assertFalse(await authorizer.revoke(generated))
        with self.assertRaises(authorization_owner.DeliveryAuthorizationError):
            await authorizer.authenticate(generated)

    async def test_capacity_identity_is_opaque_credential_and_duplicate_wins(self) -> None:
        authorizer = authorization_owner.ScopedDeliveryAuthorizer(max_principals=1)
        principal = authorization_owner.DeliveryPrincipal("principal-a")
        with self.assertRaises(ValueError):
            await authorizer.issue(principal, credential="x" * 4_097)

        registered = "credential-registered"
        await authorizer.issue(principal, credential=registered)
        differently_scoped = authorization_owner.DeliveryPrincipal(
            "principal-b",
            allowed_conversations=(contract_facade.ConversationRef("channel", "target"),),
        )
        with self.assertRaisesRegex(ValueError, "^delivery credential already exists$"):
            await authorizer.issue(differently_scoped, credential=registered)

        distinct = "credential-distinct"
        with self.assertRaisesRegex(
            ValueError,
            "^delivery credential registry capacity is exhausted$",
        ) as capacity:
            await authorizer.issue(principal, credential=distinct)
        self.assertNotIn(registered, str(capacity.exception))
        self.assertNotIn(distinct, str(capacity.exception))
        self.assertNotIn(principal.principal_id, str(capacity.exception))
        with self.assertRaises(authorization_owner.DeliveryAuthorizationError):
            await authorizer.authenticate(distinct)

        invalid = authorization_owner.DeliveryPrincipal("")
        with self.assertRaises(ValueError):
            await authorizer.issue(invalid)

    async def test_full_registry_allows_existing_authenticate_and_exact_revoke_reuse(self) -> None:
        authorizer = authorization_owner.ScopedDeliveryAuthorizer(max_principals=2)
        first = authorization_owner.DeliveryPrincipal("principal-a")
        second = authorization_owner.DeliveryPrincipal("principal-b")
        replacement = authorization_owner.DeliveryPrincipal("principal-c")
        await authorizer.issue(first, credential="credential-a")
        await authorizer.issue(second, credential="credential-b")

        self.assertEqual(await authorizer.authenticate("credential-a"), first)
        self.assertTrue(await authorizer.revoke("credential-a"))
        self.assertEqual(await authorizer.authenticate("credential-b"), second)
        with self.assertRaises(authorization_owner.DeliveryAuthorizationError):
            await authorizer.authenticate("credential-a")

        self.assertEqual(
            await authorizer.issue(replacement, credential="credential-c"),
            "credential-c",
        )
        self.assertEqual(await authorizer.authenticate("credential-c"), replacement)
        with self.assertRaisesRegex(ValueError, "capacity is exhausted"):
            await authorizer.issue(first, credential="credential-d")

    async def test_distinct_credentials_race_for_the_final_slot(self) -> None:
        authorizer = authorization_owner.ScopedDeliveryAuthorizer(max_principals=2)
        principal = authorization_owner.DeliveryPrincipal("principal-a")
        await authorizer.issue(principal, credential="occupied")

        results = await asyncio.gather(
            authorizer.issue(principal, credential="racing-a"),
            authorizer.issue(principal, credential="racing-b"),
            return_exceptions=True,
        )

        successes = [result for result in results if isinstance(result, str)]
        failures = [result for result in results if isinstance(result, ValueError)]
        self.assertEqual(len(successes), 1)
        self.assertIn(successes[0], {"racing-a", "racing-b"})
        self.assertEqual(len(failures), 1)
        self.assertEqual(
            str(failures[0]),
            "delivery credential registry capacity is exhausted",
        )

    async def test_same_credential_race_retains_duplicate_classification(self) -> None:
        authorizer = authorization_owner.ScopedDeliveryAuthorizer(max_principals=2)
        principal = authorization_owner.DeliveryPrincipal("principal-a")
        await authorizer.issue(principal, credential="occupied")

        results = await asyncio.gather(
            authorizer.issue(principal, credential="racing"),
            authorizer.issue(principal, credential="racing"),
            return_exceptions=True,
        )
        self.assertEqual(sum(result == "racing" for result in results), 1)
        self.assertEqual(sum(isinstance(result, ValueError) for result in results), 1)
        self.assertEqual(
            next(str(result) for result in results if isinstance(result, ValueError)),
            "delivery credential already exists",
        )

    async def test_cancelled_waiter_does_not_register_a_credential(self) -> None:
        authorizer = authorization_owner.ScopedDeliveryAuthorizer(max_principals=1)
        principal = authorization_owner.DeliveryPrincipal("principal-a")
        await authorizer._lock.acquire()
        try:
            waiting = asyncio.create_task(
                authorizer.issue(principal, credential="cancelled-credential")
            )
            await asyncio.sleep(0)
            self.assertFalse(waiting.done())
            waiting.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiting
        finally:
            authorizer._lock.release()

        with self.assertRaises(authorization_owner.DeliveryAuthorizationError):
            await authorizer.authenticate("cancelled-credential")
        self.assertEqual(
            await authorizer.issue(principal, credential="cancelled-credential"),
            "cancelled-credential",
        )

    async def test_fresh_authorizer_resets_process_local_capacity(self) -> None:
        principal = authorization_owner.DeliveryPrincipal("principal-a")
        original = authorization_owner.ScopedDeliveryAuthorizer(max_principals=1)
        await original.issue(principal, credential="restart-credential")
        with self.assertRaisesRegex(ValueError, "capacity is exhausted"):
            await original.issue(principal, credential="original-overflow")

        restarted = authorization_owner.ScopedDeliveryAuthorizer(max_principals=1)
        self.assertEqual(
            await restarted.issue(principal, credential="restart-credential"),
            "restart-credential",
        )
        self.assertEqual(await restarted.authenticate("restart-credential"), principal)


if __name__ == "__main__":
    unittest.main()
