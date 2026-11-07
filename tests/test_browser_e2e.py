from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")


ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("browser-e2e")
    port = _free_port()
    env = dict(os.environ)
    env.update({
        "USE_BGE": "false",
        "LLM_ENABLED": "false",
        "ATLAS_DISABLE_MODEL": "1",
        "ATLAS_SESSION_DB_PATH": str(data_dir / "sessions.db"),
        "ATLAS_EVALUATION_DB_PATH": str(data_dir / "evaluation.db"),
        "ATLAS_DATA_PLATFORM_DB_PATH": str(data_dir / "platform.db"),
        "ATLAS_PLATFORM_WAREHOUSE_DB_PATH": str(data_dir / "warehouse.db"),
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise RuntimeError(f"server failed to start: {output}")
        try:
            import httpx

            if httpx.get(url + "/api/health", timeout=0.5).status_code == 200:
                break
        except Exception:
            time.sleep(0.1)
    else:
        process.terminate()
        raise RuntimeError("server did not become ready")
    yield url
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


def test_workspace_query_renders_answer_and_sql(live_server, browser):
    context = browser.new_context()
    page = context.new_page()
    page.goto(live_server + "/workspace", wait_until="networkidle")
    page.evaluate("window.localStorage.clear()")
    page.fill("#question", "上个月华东地区销售额是多少？")
    page.click("#submit-btn")
    page.wait_for_selector(".result-answer", timeout=30_000)
    assert "销售" in page.locator(".result-answer").inner_text()
    page.locator("details.result-evidence summary").first.click()
    assert "SELECT" in page.locator(".sql-block").first.inner_text()
    page.close()
    context.close()


def test_clarification_stops_without_sql(live_server, browser):
    context = browser.new_context()
    page = context.new_page()
    page.goto(live_server + "/workspace", wait_until="networkidle")
    page.evaluate("window.localStorage.clear()")
    page.reload(wait_until="networkidle")
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.click("#new-session-btn")
    page.wait_for_timeout(300)
    page.fill("#question", "会员复购情况怎么样？")
    page.click("#submit-btn")
    page.wait_for_selector(".result-answer.result-guard", timeout=30_000)
    answer = page.locator(".result-answer.result-guard").first
    answer_text = answer.inner_text()
    assert "补充" in answer_text
    assert page.locator(".turn-card .sql-block").count() == 0
    assert errors == []
    page.close()
    context.close()


def test_follow_up_inherits_context_visibly(live_server, browser):
    context = browser.new_context()
    page = context.new_page()
    page.goto(live_server + "/workspace", wait_until="networkidle")
    page.evaluate("window.localStorage.clear()")
    page.fill("#question", "上个月华东地区销售额是多少？")
    page.click("#submit-btn")
    page.wait_for_selector(".result-answer", timeout=30_000)
    page.fill("#question", "那华南呢？")
    page.click("#submit-btn")
    page.wait_for_function(
        "document.querySelectorAll('.turn-card').length >= 2",
        timeout=30_000,
    )
    assert page.get_by_text("华南", exact=False).count() >= 1
    page.close()
    context.close()


def test_mobile_navigation_keeps_workspace_query(live_server, browser):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    page.goto(live_server + "/workspace", wait_until="networkidle")
    page.evaluate("window.localStorage.clear()")
    assert page.locator("#question").is_visible()
    page.fill("#question", "上个月销售额是多少？")
    page.click("#submit-btn")
    page.wait_for_selector(".result-answer", timeout=30_000)
    assert page.locator("#question").is_visible()
    page.close()
    context.close()
