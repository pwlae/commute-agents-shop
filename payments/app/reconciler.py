"""Every few seconds: find money we kept for orders that failed."""
import logging
import threading

import shop_http

from .metrics import CAPTURED_FOR_FAILED

log = logging.getLogger("payments.reconciler")

FAILED = {"CANCELLED", "REJECTED"}


def reconcile_once(store, orders):
    """Set the gauge; return the count, or None if orders failed."""
    count = 0
    checked = 0
    for charge_id, order_id in store.approved_unrefunded():
        try:
            r = orders.get(f"/orders/{order_id}")
        except (shop_http.ConnectError, shop_http.TimeoutError,
                shop_http.CircuitOpenError) as e:
            log.warning("orders unavailable, keeping last value: %s", e)
            return None
        if r.status == 404:
            continue
        if r.status != 200:
            log.warning("orders returned %s for order_id=%s",
                        r.status, order_id)
            return None
        checked += 1
        if r.json().get("status") in FAILED:
            count += 1
            log.warning("captured for failed order charge_id=%s"
                        " order_id=%s", charge_id, order_id)
    CAPTURED_FOR_FAILED.set(count)
    log.info("reconciled %d approved charges, %d for failed orders",
             checked, count)
    return count


def start(store, orders, every_s, stop):
    def loop():
        while not stop.wait(every_s):
            try:
                reconcile_once(store, orders)
            except Exception:
                log.exception("reconciler run failed")

    t = threading.Thread(target=loop, name="reconciler", daemon=True)
    t.start()
    return t
