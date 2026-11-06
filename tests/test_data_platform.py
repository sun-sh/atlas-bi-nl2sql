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
