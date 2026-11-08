from __future__ import annotations

import csv
import sqlite3

import pytest

from app.nl2sql import data_platform


@pytest.fixture(autouse=True)
def isolated_platform(monkeypatch, tmp_path):
    monkeypatch.setenv('ATLAS_DATA_PLATFORM_DB_PATH', str(tmp_path / 'platform.db'))
    monkeypatch.setenv('ATLAS_PLATFORM_WAREHOUSE_DB_PATH', str(tmp_path / 'warehouse.db'))
    monkeypatch.setattr(data_platform, 'ROOT', tmp_path)
    yield
    # No global state is retained; each test uses a fresh metadata database.


def test_default_workspace_sync_quality_lineage_and_service():
    data_platform.ensure_data_platform()
    overview = data_platform.platform_overview()
    assert overview['summary']['source_count'] == 1
    assert overview['summary']['metric_count'] >= 5
    synced = data_platform.sync_data_source('ds_commerce')
    assert synced['row_count'] > 0
    assert len(data_platform.list_lineage()) > 20
    quality = data_platform.run_quality('default', 'ds_commerce')
    assert quality['passed'] == 6
    assert quality['failed'] == 0
    data = data_platform.data_service_query('default', 'ds_commerce', 'orders', limit=5)
    assert data['row_count'] == 5
    assert data['limit'] == 5
    assert data_platform.platform_overview()['summary']['ready_sources'] == 1


def test_register_csv_ingest_and_query(tmp_path, monkeypatch):
    data_platform.ensure_data_platform()
    csv_dir = tmp_path / 'data' / 'imports'
    csv_dir.mkdir(parents=True)
    path = csv_dir / 'products.csv'
    with path.open('w', encoding='utf-8', newline='') as fh:
        writer = csv.writer(fh)
        writer.writerow(['sku', 'category'])
        writer.writerow(['P1', '运动鞋服'])
    source = data_platform.register_data_source(workspace_id='default', name='商品主数据', kind='csv', location='data/imports/products.csv', sync_mode='ingest')
    result = data_platform.sync_data_source(source['id'])
    assert result['row_count'] == 1
    table = result['tables'][0]['name']
    rows = data_platform.data_service_query('default', source['id'], table, limit=10)
    assert rows['rows'] == [{'sku': 'P1', 'category': '运动鞋服'}]


def test_quality_rule_catches_negative_values():
    data_platform.ensure_data_platform()
    data_platform.create_quality_rule(workspace_id='default', source_id='ds_commerce', name='订单金额非负二', rule_type='non_negative', table_name='orders', column_name='paid_amount')
    result = data_platform.run_quality('default', 'ds_commerce')
    assert result['passed'] >= 6
    assert result['failed'] == 0
    negative = [check for check in result['checks'] if check['id'].startswith('qr_') and check['name'] == '订单金额非负二']
    assert negative and negative[0]['passed'] is True


def test_workspace_role_and_path_boundaries(tmp_path):
    data_platform.ensure_data_platform()
    with pytest.raises(data_platform.PermissionDenied):
        data_platform.create_workspace(name='受限工作区', role='viewer')
    with pytest.raises(data_platform.DataPlatformError):
        data_platform.register_data_source(workspace_id='default', name='越界源', kind='csv', location='../outside.csv')
    ws = data_platform.create_workspace(name='营销分析', description='区域营销域')
    assert ws['id'].startswith('ws_')
    assert any(item['id'] == ws['id'] for item in data_platform.list_workspaces())


def test_csv_source_cannot_be_activated_as_nl2sql_database(tmp_path):
    data_platform.ensure_data_platform()
    csv_dir = tmp_path / 'data' / 'imports'
    csv_dir.mkdir(parents=True)
    path = csv_dir / 'orders.csv'
    with path.open('w', encoding='utf-8', newline='') as fh:
        writer = csv.writer(fh)
        writer.writerow(['order_id', 'paid_amount'])
        writer.writerow(['O1', '99.00'])
    source = data_platform.register_data_source(
        workspace_id='default',
        name='订单导出',
        kind='csv',
        location='data/imports/orders.csv',
        sync_mode='ingest',
    )
    data_platform.sync_data_source(source['id'])

    with pytest.raises(data_platform.DataPlatformError, match='仅用于数据服务/治理'):
        data_platform.activate_query_source(source['id'])

    listed = next(item for item in data_platform.list_data_sources() if item['id'] == source['id'])
    assert listed['query_compatibility']['compatible'] is False
    assert data_platform.active_query_database()['source_id'] == 'ds_commerce'


def test_incompatible_sqlite_source_cannot_be_activated(tmp_path):
    data_platform.ensure_data_platform()
    data_dir = tmp_path / 'data' / 'imports'
    data_dir.mkdir(parents=True)
    path = data_dir / 'inventory.db'
    conn = sqlite3.connect(path)
    try:
        conn.execute('CREATE TABLE inventory (sku TEXT PRIMARY KEY, stock INTEGER)')
        conn.execute("INSERT INTO inventory VALUES ('P1', 4)")
        conn.commit()
    finally:
        conn.close()
    source = data_platform.register_data_source(
        workspace_id='default',
        name='库存库',
        kind='sqlite',
        location='data/imports/inventory.db',
    )
    data_platform.sync_data_source(source['id'])

    listed = next(item for item in data_platform.list_data_sources() if item['id'] == source['id'])
    assert listed['query_compatibility']['compatible'] is False
    assert 'orders' in listed['query_compatibility']['missing_tables']
    with pytest.raises(data_platform.DataPlatformError, match='缺少 NL2SQL 所需业务表'):
        data_platform.activate_query_source(source['id'])
    with pytest.raises(data_platform.DataPlatformError, match='缺少表：orders'):
        data_platform.activate_query_source(source['id'])


def test_synced_source_can_become_active_query_database(monkeypatch, tmp_path):
    from app.nl2sql import data_platform

    monkeypatch.setenv("ATLAS_DATA_PLATFORM_DB_PATH", str(tmp_path / "platform.db"))
    monkeypatch.setenv("ATLAS_PLATFORM_WAREHOUSE_DB_PATH", str(tmp_path / "warehouse.db"))
    data_platform.init_data_platform()
    source = data_platform.get_data_source("ds_commerce")
    data_platform.sync_data_source(source["id"])
    activated = data_platform.activate_query_source(source["id"])
    assert activated["source"]["active_for_query"] is True
    assert activated["read_only"] is True
    active = data_platform.active_query_database()
    assert active["source_id"] == source["id"]
    assert active["fallback"] is False


def test_data_service_hides_restricted_columns_and_unknown_tables():
    data_platform.ensure_data_platform()
    data_platform.sync_data_source('ds_commerce')
    result = data_platform.data_service_query('default', 'ds_commerce', 'orders', limit=2)
    assert 'customer_id' not in result['columns']
    assert all('customer_id' not in row for row in result['rows'])
    with pytest.raises(data_platform.DataPlatformError, match='数据服务表不存在'):
        data_platform.data_service_query('default', 'ds_commerce', 'missing_table')


def test_external_database_source_requires_readonly_dsn_and_never_exposes_it():
    data_platform.ensure_data_platform()
    with pytest.raises(data_platform.DataPlatformError, match='只读 DSN'):
        data_platform.register_data_source(
            workspace_id='default',
            name='外部订单库',
            kind='postgresql',
            location='',
        )
    source = data_platform.register_data_source(
        workspace_id='default',
        name='外部订单库',
        kind='postgresql',
        location='',
        dsn='postgresql://readonly:secret@db.internal/atlas',
    )
    assert source['dsn_configured'] is True
    assert 'secret' not in str({k: v for k, v in source.items() if k != '_dsn'})
    synced = data_platform.sync_data_source(source['id'])
    assert synced['tables'] == []
    compatibility = data_platform.inspect_external_schema(source['_dsn'] if '_dsn' in source else '', 'postgresql')
    assert compatibility['compatible'] is False
    with pytest.raises(data_platform.DataPlatformError, match='驱动未安装'):
        data_platform.activate_query_source(source['id'])


def test_ready_external_source_never_leaks_dsn_in_public_or_overview_payloads(monkeypatch):
    from types import SimpleNamespace

    source = data_platform.register_data_source(
        workspace_id='default',
        name='外部订单库',
        kind='postgresql',
        location='',
        dsn='postgresql://readonly:p%40ssword@db.internal/atlas',
    )
    # Force readiness without contacting a real database, then prove that the
    # public list and overview payloads omit both the private key and secret.
    with data_platform._connect() as conn:
        conn.execute("UPDATE data_sources SET status='ready' WHERE id=?", (source['id'],))
    monkeypatch.setattr(
        data_platform,
        'inspect_external_schema',
        lambda dsn, kind: {'compatible': True, 'missing_tables': [], 'missing_columns': {}},
    )
    listed = data_platform.list_data_sources()
    external = next(item for item in listed if item['id'] == source['id'])
    assert '_dsn' not in external
    assert 'p@ssword' not in str(external)
    overview = data_platform.platform_overview()
    assert 'p@ssword' not in str(overview)
    assert '_dsn' not in str(overview)


def test_dbapi_compat_probe_keeps_secret_out_of_returned_source(monkeypatch):
    source = data_platform.register_data_source(
        workspace_id='default',
        name='MySQL 订单库',
        kind='mysql',
        location='',
        dsn='mysql://reader:secret@db.internal/commerce',
    )
    with data_platform._connect() as conn:
        conn.execute("UPDATE data_sources SET status='ready' WHERE id=?", (source['id'],))
    captured = {}

    def inspect(dsn, kind):
        captured['dsn'] = dsn
        captured['kind'] = kind
        return {'compatible': True, 'missing_tables': [], 'missing_columns': {}}

    monkeypatch.setattr(data_platform, 'inspect_external_schema', inspect)
    external = next(item for item in data_platform.list_data_sources() if item['id'] == source['id'])
    assert captured == {
        'dsn': 'mysql://reader:secret@db.internal/commerce',
        'kind': 'mysql',
    }
    assert '_dsn' not in external
    assert 'secret' not in str(external)


def test_external_schema_inspection_accepts_contract_and_reports_missing_columns(monkeypatch):
    import sys
    from types import SimpleNamespace

    class Cursor:
        def __init__(self, external):
            self.external = external
            self.rows = []

        def execute(self, sql, params=None):
            table = params[0] if params else ""
            columns = self.external.get(table)
            self.rows = [] if columns is None else [(column,) for column in columns]

        def fetchall(self):
            return self.rows

        def close(self):
            pass

    class Connection:
        def __init__(self, external):
            self.external = external

        def cursor(self):
            return Cursor(self.external)

        def close(self):
            pass

    complete = {
        "orders": ("order_id", "order_date", "status", "channel", "region", "city", "category", "product_id", "quantity", "paid_amount", "discount_amount", "customer_id"),
        "order_items": ("order_id", "line_no", "product_id", "product_name", "category", "quantity", "unit_price", "cost_price"),
        "refunds": ("refund_id", "order_id", "request_date", "refund_amount", "reason", "status"),
        "marketing_spend": ("spend_date", "channel", "campaign", "spend", "impressions", "clicks", "attributed_orders"),
        "customers": ("customer_id", "register_date", "member_level", "city", "first_channel"),
    }
    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=lambda dsn, autocommit=True: Connection(complete)))
    report = data_platform.inspect_external_schema("postgresql://readonly@db/atlas", "postgresql")
    assert report["compatible"] is True
    incomplete = dict(complete)
    incomplete["orders"] = tuple(column for column in complete["orders"] if column != "paid_amount")
    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=lambda dsn, autocommit=True: Connection(incomplete)))
    report = data_platform.inspect_external_schema("postgresql://readonly@db/atlas", "postgresql")
    assert report["compatible"] is False
    assert report["missing_columns"]["orders"] == ["paid_amount"]
