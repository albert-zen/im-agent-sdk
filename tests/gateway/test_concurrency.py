from __future__ import annotations

import ast
import asyncio
import importlib.util
import subprocess
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import imagent
import imagent.gateway as gateway_facade
from imagent.gateway import concurrency as concurrency_owner
from imagent.gateway.concurrency import KeyedLockCapacityError, KeyedLockRegistry


@dataclass(frozen=True, slots=True)
class _StableKey:
    value: str


class GatewayConcurrencyOwnerTests(unittest.TestCase):
    def test_one_dependency_neutral_owner_replaces_the_historical_module(self) -> None:
        self.assertIs(KeyedLockRegistry, concurrency_owner.KeyedLockRegistry)
        self.assertIs(KeyedLockCapacityError, concurrency_owner.KeyedLockCapacityError)
        self.assertEqual(KeyedLockRegistry.__module__, "imagent.gateway.concurrency")
        self.assertEqual(KeyedLockCapacityError.__module__, "imagent.gateway.concurrency")
        self.assertNotIn("KeyedLockRegistry", gateway_facade.__dict__)
        self.assertNotIn("KeyedLockCapacityError", gateway_facade.__dict__)
        self.assertNotIn("KeyedLockRegistry", imagent.__dict__)
        self.assertIsNone(importlib.util.find_spec("imagent.keyed_locks"))

        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import importlib.util; "
                    "import imagent.gateway.concurrency; "
                    "assert importlib.util.find_spec('imagent.keyed_locks') is None"
                ),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

        source = Path(concurrency_owner.__file__).read_text(encoding="utf-8")
        imports: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imports.update(alias.name.partition(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0)
                if node.module is not None:
                    imports.add(node.module.partition(".")[0])
        self.assertLessEqual(imports, sys.stdlib_module_names | {"__future__"})

    def test_optional_capacity_accepts_only_positive_non_boolean_integers(self) -> None:
        self.assertIsNone(KeyedLockRegistry().capacity)
        self.assertEqual(KeyedLockRegistry(max_active_keys=2).capacity, 2)

        for invalid in (0, -1, True, 1.5, "1"):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    KeyedLockRegistry(max_active_keys=cast(int, invalid))


class KeyedLockRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_equal_stable_keys_join_at_capacity_and_distinct_key_fails(self) -> None:
        registry = KeyedLockRegistry(max_active_keys=1)
        owner_entered = asyncio.Event()
        release_owner = asyncio.Event()
        waiter_entered = asyncio.Event()

        async def owner() -> None:
            async with registry.hold(_StableKey("same")):
                owner_entered.set()
                await release_owner.wait()

        async def waiter() -> None:
            async with registry.hold(_StableKey("same")):
                waiter_entered.set()

        owner_task = asyncio.create_task(owner())
        await owner_entered.wait()
        waiter_task = asyncio.create_task(waiter())
        await asyncio.sleep(0)

        self.assertEqual(registry.active_key_count, 1)
        self.assertFalse(waiter_entered.is_set())
        with self.assertRaisesRegex(KeyedLockCapacityError, "capacity=1"):
            async with registry.hold(_StableKey("distinct")):
                self.fail("a distinct key must fail before caller work")

        release_owner.set()
        await asyncio.gather(owner_task, waiter_task)
        self.assertTrue(waiter_entered.is_set())
        self.assertEqual(registry.active_key_count, 0)

    async def test_independent_keys_progress_concurrently(self) -> None:
        registry = KeyedLockRegistry(max_active_keys=2)
        second_entered = asyncio.Event()
        release_second = asyncio.Event()

        async def second() -> None:
            async with registry.hold("second"):
                second_entered.set()
                await release_second.wait()

        async with registry.hold("first"):
            second_task = asyncio.create_task(second())
            await asyncio.wait_for(second_entered.wait(), timeout=1)
            self.assertEqual(registry.active_key_count, 2)
            release_second.set()
            await second_task

        self.assertEqual(registry.active_key_count, 0)

    async def test_owner_failure_and_cancellation_release_the_exact_entry(self) -> None:
        registry = KeyedLockRegistry(max_active_keys=1)

        with self.assertRaisesRegex(RuntimeError, "owner failed"):
            async with registry.hold("failed"):
                raise RuntimeError("owner failed")
        self.assertEqual(registry.active_key_count, 0)

        entered = asyncio.Event()
        never_release = asyncio.Event()

        async def cancelled_owner() -> None:
            async with registry.hold("cancelled"):
                entered.set()
                await never_release.wait()

        owner_task = asyncio.create_task(cancelled_owner())
        await entered.wait()
        owner_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await owner_task
        self.assertEqual(registry.active_key_count, 0)

        async with registry.hold("reused"):
            self.assertEqual(registry.active_key_count, 1)
        self.assertEqual(registry.active_key_count, 0)

    async def test_cancelled_waiter_cannot_unlock_or_split_the_owner_lane(self) -> None:
        registry = KeyedLockRegistry(max_active_keys=1)
        owner_entered = asyncio.Event()
        release_owner = asyncio.Event()
        waiter_started = asyncio.Event()
        successor_entered = asyncio.Event()

        async def owner() -> None:
            async with registry.hold("shared"):
                owner_entered.set()
                await release_owner.wait()

        async def waiter() -> None:
            waiter_started.set()
            async with registry.hold("shared"):
                self.fail("the cancelled waiter must not enter")

        async def successor() -> None:
            async with registry.hold("shared"):
                successor_entered.set()

        owner_task = asyncio.create_task(owner())
        await owner_entered.wait()
        waiter_task = asyncio.create_task(waiter())
        await waiter_started.wait()
        await asyncio.sleep(0)

        waiter_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter_task
        self.assertEqual(registry.active_key_count, 1)

        successor_task = asyncio.create_task(successor())
        await asyncio.sleep(0)
        self.assertFalse(successor_entered.is_set())
        with self.assertRaises(KeyedLockCapacityError):
            async with registry.hold("distinct"):
                self.fail("waiter cancellation must not evict the owner")

        release_owner.set()
        await asyncio.gather(owner_task, successor_task)
        self.assertTrue(successor_entered.is_set())
        self.assertEqual(registry.active_key_count, 0)
