"""
tests/test_phase14_delivery_zones.py — Phase 14
Tests: DeliveryZone model, admin CRUD, public listing, tenant isolation.
"""
import pytest
from models.delivery_zones import DeliveryZone
from models.tenant import Location


@pytest.fixture
def zone(db, location):
    z = DeliveryZone(
        location_id=location.id,
        name="Центр",
        fee=15000,
        min_order=50000,
        eta_minutes=30,
        is_active=True,
        sort_order=1,
    )
    db.add(z)
    db.commit()
    db.refresh(z)
    yield z
    try:
        db.delete(z)
        db.commit()
    except Exception:
        db.rollback()


class TestDeliveryZoneModel:
    def test_zone_created(self, zone):
        assert zone.id is not None
        assert zone.name == "Центр"
        assert zone.fee == 15000
        assert zone.min_order == 50000
        assert zone.eta_minutes == 30
        assert zone.is_active is True

    def test_zone_location_relationship(self, db, zone, location):
        loaded = db.query(DeliveryZone).filter(DeliveryZone.id == zone.id).first()
        assert loaded.location_id == location.id

    def test_inactive_zone_filter(self, db, zone):
        zone.is_active = False
        db.commit()
        active = db.query(DeliveryZone).filter(
            DeliveryZone.id == zone.id,
            DeliveryZone.is_active == True,
        ).first()
        assert active is None
        zone.is_active = True
        db.commit()


class TestDeliveryZoneAdminAPI:
    def test_create_zone(self, admin_client, location):
        resp = admin_client.post(
            f"/api/restaurants/{location.restaurant_id}/delivery-zones",
            params={"location_id": location.id},
            json={"name": "Юнусабад", "fee": 20000, "min_order": 0, "sort_order": 0},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Юнусабад"
        assert data["fee"] == 20000

    def test_list_zones_admin(self, admin_client, location, zone):
        resp = admin_client.get(
            f"/api/restaurants/{location.restaurant_id}/delivery-zones",
            params={"location_id": location.id},
        )
        assert resp.status_code == 200
        names = [z["name"] for z in resp.json()]
        assert "Центр" in names

    def test_update_zone(self, admin_client, zone):
        resp = admin_client.patch(
            f"/api/delivery-zones/{zone.id}",
            json={"fee": 25000},
        )
        assert resp.status_code == 200
        assert resp.json()["fee"] == 25000

    def test_deactivate_zone(self, admin_client, zone):
        resp = admin_client.delete(f"/api/delivery-zones/{zone.id}")
        assert resp.status_code == 204

    def test_cross_restaurant_zone_rejected(self, admin_client2, zone):
        """Admin from different restaurant cannot access zone."""
        resp = admin_client2.patch(
            f"/api/delivery-zones/{zone.id}",
            json={"fee": 99999},
        )
        assert resp.status_code in (403, 404)


class TestDeliveryZonePublicAPI:
    def test_public_list_active_only(self, client, location, zone):
        resp = client.get(f"/api/locations/{location.slug}/delivery-zones")
        assert resp.status_code == 200
        data = resp.json()
        assert any(z["name"] == "Центр" for z in data)
        # Only active zones returned
        assert all(z.get("is_active", True) for z in data)

    def test_inactive_zone_not_in_public_list(self, client, db, location, zone):
        zone.is_active = False
        db.commit()
        resp = client.get(f"/api/locations/{location.slug}/delivery-zones")
        assert resp.status_code == 200
        names = [z["name"] for z in resp.json()]
        assert "Центр" not in names
        zone.is_active = True
        db.commit()

    def test_invalid_slug_returns_404(self, client):
        resp = client.get("/api/locations/nonexistent-slug-xyz/delivery-zones")
        assert resp.status_code == 404
