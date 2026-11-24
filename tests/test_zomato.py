"""Zomato workflow: request parsing and the never-auto-approve purchase gate (no network)."""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from seguro.skills.library.demo_skills import parse_zomato_request
from seguro.core.workflow.gates import ConfirmationBroker


def test_order_request_parses_items_restaurant_city_address():
    spec = parse_zomato_request("Order 2 Misal Slice and 1 Batata Chiwda from Bedekar Tea Stall in Pune on Zomato "
                                "to my Home address")
    assert spec["restaurant"] == "Bedekar Tea Stall" and spec["city"] == "Pune"
    assert spec["items"] == [{"name": "Misal Slice", "qty": 2}, {"name": "Batata Chiwda", "qty": 1}]
    assert spec["place"] is True and spec["address"] == "Home"


def test_cart_wording_never_places_an_order():
    spec = parse_zomato_request("Add 2 Misal Slice to my cart from Bedekar Tea Stall in Pune on Zomato")
    assert spec["place"] is False and spec["items"] == [{"name": "Misal Slice", "qty": 2}]
    assert parse_zomato_request("Order 1 Dahi from Bedekar Tea Stall in Pune, don't place it")["place"] is False


def test_default_address_is_home_and_missing_restaurant_is_an_error():
    assert parse_zomato_request("Order 1 Dahi from Bedekar Tea Stall in Pune on Zomato")["address"] == "Home"
    with pytest.raises(ValueError):
        parse_zomato_request("Order some food on Zomato")


@pytest.mark.asyncio
async def test_purchase_can_never_be_pre_approved():
    b = ConfirmationBroker()
    asked = []

    async def deny(reason, details):
        asked.append(reason)
        return False
    b.configure(responder=deny, auto_approve={"zomato_place_order", "settings_set_theme"})
    assert await b.confirm("order", tool="zomato_place_order") is False and asked   # human was asked, said no
    assert await b.confirm("theme", tool="settings_set_theme") is True                # normal pre-approval still works
