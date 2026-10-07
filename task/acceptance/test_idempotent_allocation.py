"""Acceptance check for the Stage 1 task: allocation must be idempotent.

This file lives OUTSIDE the agent's writable workspace. The harness mounts it
read-only into the sandbox only during final verification.

Business rule: an order line (same orderid, sku and qty) is allocated at most
once. Re-sending the same Allocate command (for example, a message delivered
twice by the broker) must not reserve stock again, must not emit a second
Allocated event and must not add a duplicate row to the allocations read model.
"""

from datetime import date
from unittest import mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import clear_mappers, sessionmaker

from allocation import bootstrap, views
from allocation.adapters.orm import metadata
from allocation.domain import commands, events
from allocation.domain.model import Batch, OrderLine, Product
from allocation.service_layer import unit_of_work

today = date.today()


@pytest.fixture
def bus():
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    published = []
    message_bus = bootstrap.bootstrap(
        start_orm=True,
        uow=unit_of_work.SqlAlchemyUnitOfWork(session_factory),
        notifications=mock.Mock(),
        publish=lambda channel, event: published.append((channel, event)),
    )
    message_bus.published = published
    yield message_bus
    clear_mappers()


# --- domain level ----------------------------------------------------------


def test_product_allocating_the_same_line_twice_returns_the_same_batch():
    batch = Batch("b1", "LAMP", 100, eta=None)
    product = Product("LAMP", [batch])
    line = OrderLine("o1", "LAMP", 10)

    assert product.allocate(line) == "b1"
    assert product.allocate(line) == "b1"
    assert batch.available_quantity == 90


def test_product_does_not_emit_a_second_allocated_event():
    product = Product("LAMP", [Batch("b1", "LAMP", 100, eta=None)])
    line = OrderLine("o1", "LAMP", 10)

    product.allocate(line)
    product.allocate(line)

    allocated = [e for e in product.events if isinstance(e, events.Allocated)]
    assert len(allocated) == 1


def test_duplicate_line_does_not_spill_into_a_second_batch():
    in_stock = Batch("in-stock", "LAMP", 10, eta=None)
    shipment = Batch("shipment", "LAMP", 10, eta=today)
    product = Product("LAMP", [in_stock, shipment])
    line = OrderLine("o1", "LAMP", 10)

    assert product.allocate(line) == "in-stock"
    assert product.allocate(line) == "in-stock"
    assert shipment.available_quantity == 10


def test_a_different_order_is_still_allocated_normally():
    product = Product("LAMP", [Batch("b1", "LAMP", 100, eta=None)])

    product.allocate(OrderLine("o1", "LAMP", 10))
    product.allocate(OrderLine("o2", "LAMP", 10))

    assert product.batches[0].available_quantity == 80


# --- across message bus, handlers, ORM and read model ----------------------


def test_redelivered_allocate_command_keeps_one_row_in_the_read_model(bus):
    bus.handle(commands.CreateBatch("b1", "LAMP", 100, None))
    bus.handle(commands.Allocate("o1", "LAMP", 10))
    bus.handle(commands.Allocate("o1", "LAMP", 10))

    assert views.allocations("o1", bus.uow) == [{"sku": "LAMP", "batchref": "b1"}]
    assert len(bus.published) == 1


def test_redelivered_allocate_command_does_not_reserve_a_second_batch(bus):
    bus.handle(commands.CreateBatch("in-stock", "LAMP", 10, None))
    bus.handle(commands.CreateBatch("shipment", "LAMP", 10, today))
    bus.handle(commands.Allocate("o1", "LAMP", 10))
    bus.handle(commands.Allocate("o1", "LAMP", 10))

    assert views.allocations("o1", bus.uow) == [{"sku": "LAMP", "batchref": "in-stock"}]
    with bus.uow:
        product = bus.uow.products.get(sku="LAMP")
        shipment = next(b for b in product.batches if b.reference == "shipment")
        assert shipment.available_quantity == 10
