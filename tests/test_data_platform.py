from __future__ import annotations

import csv

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
