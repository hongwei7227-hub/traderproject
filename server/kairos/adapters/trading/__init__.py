"""Talking to the execution worker.

Outbound through a table the relay drains; inbound by reading the orders the
worker writes. Neither direction is a live call to the worker, which is what
lets an order survive the worker being restarted mid-flight.
"""

from kairos.adapters.trading.orders import ExecutionOrders, order_table
from kairos.adapters.trading.outbox import OutboxGateway, OutboxRelay

__all__ = ["ExecutionOrders", "OutboxGateway", "OutboxRelay", "order_table"]
