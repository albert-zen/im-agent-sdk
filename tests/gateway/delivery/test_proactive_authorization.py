from __future__ import annotations

import asyncio
import unittest

import imagent.adapters as adapter_facade
import imagent.contracts as contract_facade
import imagent.gateway.delivery as delivery_facade
import imagent.proactive_delivery as historical_mixed_module
from imagent.gateway.delivery import proactive_authorization as authorization_owner


class ProactiveAuthorizationOwnershipTests(unittest.TestCase):
    def test_facades_use_exact_owner_objects_and_old_symbols_are_absent(self) -> None:
        self.assertIs(
            delivery_facade.DeliveryAuthorizer,
            authorization_owner.DeliveryAuthorizer,
        )
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
        self.assertFalse(hasattr(historical_mixed_module, "ScopedDeliveryAuthorizer"))
        self.assertFalse(hasattr(historical_mixed_module, "DeliveryAuthorizationError"))


class ScopedDeliveryAuthorizerTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_validation_bounds_duplicates_and_concurrent_issue(self) -> None:
        authorizer = authorization_owner.ScopedDeliveryAuthorizer()
        principal = authorization_owner.DeliveryPrincipal("principal-a")
        with self.assertRaises(ValueError):
            await authorizer.issue(principal, credential="x" * 4_097)
        await authorizer.issue(principal, credential="stable")
        with self.assertRaises(ValueError):
            await authorizer.issue(principal, credential="stable")

        results = await asyncio.gather(
            authorizer.issue(principal, credential="racing"),
            authorizer.issue(principal, credential="racing"),
            return_exceptions=True,
        )
        self.assertEqual(sum(result == "racing" for result in results), 1)
        self.assertEqual(sum(isinstance(result, ValueError) for result in results), 1)

        invalid = authorization_owner.DeliveryPrincipal("")
        with self.assertRaises(ValueError):
            await authorizer.issue(invalid)


if __name__ == "__main__":
    unittest.main()
