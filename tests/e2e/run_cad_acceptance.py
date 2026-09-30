from pathlib import Path
from playwright.sync_api import sync_playwright, expect
import subprocess
from datetime import datetime, UTC
from contextlib import contextmanager
import os
import sys
import json
import socket
import threading
import tempfile
import traceback
import re
import argparse

parser = argparse.ArgumentParser(
    description="Real 3D + CAD browser acceptance; synthetic data only"
)
parser.add_argument("--evidence", type=Path, required=True)
parser.add_argument("--dist", type=Path)
args = parser.parse_args()
repo = Path(__file__).resolve().parents[2]
api = repo / "apps/api"
root = repo.parent
build_dist = args.dist.resolve() if args.dist else repo / "apps/web-react/dist"
out = args.evidence.resolve() / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
out.mkdir(parents=True)
source_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
steps = []
errors = []
writes = []
page = None


@contextmanager
def case(name):
    item = {"case": name, "status": "RUNNING"}
    steps.append(item)
    try:
        yield
        item["status"] = "PASS"
        print("PASS " + name, flush=True)
    except Exception as exc:
        item.update(status="FAIL", error=str(exc), traceback=traceback.format_exc())
        if page:
            page.screenshot(path=str(out / "failure.png"), full_page=True)
            (out / "failure.txt").write_text(page.locator("body").inner_text())
            (out / "failure-aria.txt").write_text(page.locator("body").aria_snapshot())
        raise
    finally:
        (out / "results.json").write_text(
            json.dumps(
                {
                    "baseline": "d34b63117ffc50a81ec86c0901a13dbdc579323c",
                    "source_commit": source_commit,
                    "cases": steps,
                    "page_errors": errors,
                    "writes": writes,
                    "scope": "Fresh synthetic SQLite, actual browser UI actions; not original or 18001 preview database",
                },
                ensure_ascii=False,
                indent=2,
            )
        )


def camera_pose():
    return page.locator("canvas").evaluate("""el => {
   const key=Object.getOwnPropertyNames(el).find(k=>k.startsWith('__reactFiber$'));
   for(let f=el[key],n=0; f && n<40; f=f.return,n++) {
     const scene=f.memoizedProps?.engine?.current;
     if(scene?.camera?.isPerspectiveCamera) return {position:scene.camera.position.toArray(),target:scene.controls.target.toArray()};
   }
   throw Error('Camera read-only evidence unavailable');
 }""")


def button(name):
    return page.get_by_role(
        "button",
        name=re.compile(
            r"(?:^|\s)" + r"\s*".join(re.escape(c) for c in name if not c.isspace()) + "$"
        ),
    )


def click(name):
    button(name).click()


def field(name, value):
    page.get_by_label(name, exact=True).fill(str(value))


def choose(label, text):
    loc = page.get_by_role("combobox", name=re.compile(re.escape(label) + "$"))
    loc.click()
    loc.fill(text)
    page.locator(".ant-select-dropdown:visible .ant-select-item-option-content").filter(
        has_text=text
    ).first.click()


def new(kind):
    click("新建")
    page.get_by_role("menuitem", name=kind, exact=True).click()


def save(name, path):
    with page.expect_response(
        lambda r: r.request.method == "POST" and r.url.endswith(path)
    ) as response:
        click(name)
    response = response.value
    assert response.status in (200, 201), (response.status, response.text())
    page.get_by_role("dialog").wait_for(state="hidden")
    return response.json()


try:
    with tempfile.TemporaryDirectory(prefix="mature-3d-test-") as temporary:
        temp = Path(temporary)
        os.chdir(temp)
        sys.path.insert(0, str(api))
        db = "sqlite+pysqlite:///" + str(temp / "synthetic.sqlite3")
        os.environ.update(
            {
                "PYTHONDONTWRITEBYTECODE": "1",
                "DATABASE_URL": db,
                "PLATFORM_DATABASE_URL": db,
                "MIGRATION_DATABASE_URL": db,
                "RATE_LIMIT_DATABASE_URL": db,
                "OBJECT_STORAGE_PATH": str(temp / "storage"),
                "APP_ENV": "test",
                "AUTH_MODE": "demo",
                "DEMO_MODE": "true",
                "SSO_ENABLED": "false",
                "REQUIRE_HTTPS": "false",
                "RATE_LIMIT_ENABLED": "true",
                "RATE_LIMIT_BACKEND": "memory",
                "ALLOWED_HOSTS": '["127.0.0.1","localhost"]',
            }
        )
        from alembic.config import Config
        from alembic import command

        cfg = Config(str(api / "alembic.ini"))
        cfg.set_main_option("script_location", str(api / "migrations"))
        command.upgrade(cfg, "head")
        from app.seed import seed

        seed()
        from app.main import app, SPAStaticFiles

        for route in app.routes:
            if getattr(route, "path", None) == "/app-next":
                route.app = SPAStaticFiles(directory=build_dist, html=True)
        import uvicorn

        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        base = "http://127.0.0.1:" + str(sock.getsockname()[1])
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                channel="chromium",
                headless=True,
                chromium_sandbox=True,
                ignore_default_args=True,
                args=[
                    "--headless=new",
                    f"--user-data-dir={temp}/profile",
                    "--remote-debugging-pipe",
                    "--no-startup-window",
                    "--no-first-run",
                ],
            )
            page = browser.new_page(viewport={"width": 1800, "height": 1100})
            page.set_default_timeout(15000)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on(
                "response",
                lambda r: writes.append(
                    {
                        "method": r.request.method,
                        "path": r.url.removeprefix(base),
                        "status": r.status,
                        "body": r.request.post_data
                        if not r.request.headers.get("content-type", "").startswith("multipart/")
                        else "[CAD file upload omitted]",
                    }
                )
                if r.request.method not in ["GET", "OPTIONS"]
                else None,
            )
            with case("open_correct_3d_directory_and_cancel_room"):
                page.goto(base + "/app-next/3d")
                click("创建房间")
                field("对象编号", "BASE-CANCEL")
                field("名称", "Cancelled room")
                click("取消")
                expect(page.get_by_role("dialog")).not_to_be_visible()
                assert not any(w["path"].endswith("/scene/rooms") for w in writes)
            with case("create_room_through_ui"):
                click("创建房间")
                field("对象编号", "BASE-ROOM")
                field("名称", "Baseline 3D room")
                created = save("保存房间", "/api/v1/scene/rooms")
                click("进入房间3D")
                expect(page.get_by_role("button", name="适配全部对象", exact=True)).to_be_enabled()
            with case("create_two_racks_through_ui"):
                new("批量放置机柜")
                field("机柜编号前缀", "BASE-RACK")
                field("机柜名称前缀", "Baseline rack")
                field("机柜数量", 2)
                field("每行机柜数量", 2)
                save("保存机柜", "/api/v1/scene/racks")
                expect(button("新建")).to_be_enabled()
            with case("create_devices_and_template_ports_through_ui"):
                for name, rack, template, start in [
                    ("BASE-SW-A", "BASE-RACK-01", "标准交换机", 20),
                    ("BASE-PP-B", "BASE-RACK-02", "标准铜配线架", 20),
                    ("BASE-SW-C", "BASE-RACK-02", "标准交换机", 16),
                ]:
                    new("安装设备")
                    field("对象编号", name)
                    field("名称", name)
                    choose("安装机柜", rack)
                    choose("设备模板", template)
                    field("起始 U 位", start)
                    save("保存设备", "/api/v1/scene/devices")
                    expect(button("新建")).to_be_enabled()
            with case("create_xyz_pathway_through_ui"):
                new("线槽 / 桥架")
                field("对象编号", "BASE-TRAY")
                field("名称", "Baseline XYZ tray")
                for i, p in enumerate([(1, 1, 2.5), (2.4, 1, 2.5), (2.4, 2, 1.5)], 1):
                    for axis, value in zip(["X", "Y", "高度"], p):
                        field(f"点 {i} {axis}", value)
                save("保存线槽", "/api/v1/scene/pathways")
                expect(page.get_by_role("button", name="适配全部对象", exact=True)).to_be_enabled()
            with case("actual_canvas_orbit_zoom_and_projection"):
                click("适配全部对象")
                canvas = page.locator("canvas")
                before = canvas.screenshot()
                box = canvas.bounding_box()
                x, y = box["x"] + box["width"] * 0.55, box["y"] + box["height"] * 0.55
                page.mouse.move(x, y)
                page.mouse.down()
                page.mouse.move(x + 130, y + 70, steps=15)
                page.mouse.up()
                page.mouse.wheel(0, -170)
                page.get_by_role("button", name="正 面", exact=True).click()
                assert canvas.screenshot() != before
                click("等 轴")
                click("适配全部对象")
                page.screenshot(path=str(out / "created-3d-resources.png"), full_page=True)
            with case("cancel_connection_leaves_no_cable_write"):
                click("端口接线")
                field("线缆编号", "BASE-CANCEL-CABLE")
                click("取消")
                assert not any(w["path"].endswith("/scene/cables") for w in writes)
            with case("cross_rack_connection_preview_and_save"):
                click("端口接线")
                choose("A 端端口", "BASE-SW-A")
                choose("B 端端口", "BASE-PP-B")
                field("线缆编号", "BASE-CABLE-AB")
                expect(
                    page.get_by_role("button", name="确认连接并保存", exact=True)
                ).to_be_enabled()
                page.screenshot(path=str(out / "cross-rack-preview.png"), full_page=True)
                with page.expect_response(
                    lambda r: r.request.method == "POST" and r.url.endswith("/scene/cables")
                ) as response:
                    click("确认连接并保存")
                assert response.value.status == 201, response.value.text()
                saved = response.value.json()
                expect(
                    page.get_by_role("heading", name="BASE-CABLE-AB", exact=True)
                ).to_be_visible()
            with case("reload_backend_saved_route"):
                page.reload()
                expect(
                    page.get_by_role("heading", name="BASE-CABLE-AB", exact=True)
                ).to_be_visible()
                expect(page.get_by_role("button", name="适配全部对象", exact=True)).to_be_enabled()
                page.screenshot(path=str(out / "saved-route-reloaded.png"), full_page=True)
            with case("camera_space_up_shift_down_and_key_release"):
                expect(button("新建")).to_be_enabled()
                click("定位到连接")
                click("正 面")
                canvas = page.locator("canvas")
                canvas.focus()
                page.wait_for_timeout(100)
                start_pose = camera_pose()
                before = canvas.screenshot(path=str(out / "camera-before.png"))
                scroll = page.evaluate("window.scrollY")
                n = len(writes)
                page.keyboard.down("Space")
                page.wait_for_timeout(600)
                page.keyboard.up("Space")
                page.wait_for_timeout(100)
                raised_pose = camera_pose()
                assert raised_pose["position"][1] > start_pose["position"][1]
                raised = canvas.screenshot(path=str(out / "camera-space-up.png"))
                assert raised != before
                page.wait_for_timeout(150)
                assert camera_pose() == raised_pose, "camera moved after keyup"
                page.keyboard.down("Shift")
                page.wait_for_timeout(600)
                page.keyboard.up("Shift")
                page.wait_for_timeout(100)
                lowered_pose = camera_pose()
                assert lowered_pose["position"][1] < raised_pose["position"][1]
                lowered = canvas.screenshot(path=str(out / "camera-shift-down.png"))
                assert lowered != raised
                (out / "camera-pose-evidence.json").write_text(
                    json.dumps(
                        {"before": start_pose, "space": raised_pose, "shift": lowered_pose},
                        indent=2,
                    )
                )
                page.wait_for_timeout(150)
                assert camera_pose() == lowered_pose
                assert page.evaluate("window.scrollY") == scroll
                assert len(writes) == n
            with case("camera_focus_loss_and_form_space_semantics"):
                canvas.focus()
                page.keyboard.down("Space")
                page.wait_for_timeout(150)
                button("新建").focus()
                page.wait_for_timeout(100)
                stopped = camera_pose()
                page.wait_for_timeout(150)
                assert camera_pose() == stopped
                page.keyboard.up("Space")
                new("房间 / Server Room")
                field("对象编号", "DO-NOT-SAVE")
                field("名称", "Form")
                name = page.get_by_label("名称", exact=True)
                name.press("End")
                name.press("Space")
                name.press_sequentially("value")
                expect(name).to_have_value("Form value")
                page.wait_for_timeout(350)
                covered = camera_pose()
                name.press("Shift")
                page.wait_for_timeout(100)
                assert camera_pose() == covered
                click("取消")
                expect(page.get_by_role("dialog")).not_to_be_visible()
            with case("camera_movement_disabled_during_connection"):
                click("端口接线")
                canvas.focus()
                page.wait_for_timeout(350)
                frozen = camera_pose()
                scroll = page.evaluate("window.scrollY")
                page.keyboard.down("Space")
                page.wait_for_timeout(200)
                page.keyboard.up("Space")
                page.keyboard.down("Shift")
                page.wait_for_timeout(200)
                page.keyboard.up("Shift")
                assert camera_pose() == frozen
                assert page.evaluate("window.scrollY") == scroll
                click("取消")
                page.screenshot(path=str(out / "camera-updated-workspace.png"), full_page=True)
            with case("cad_export_native_dxf_and_ifc_from_3d"):
                click("CAD 文件同步")
                expect(page.get_by_role("button", name="导出 DXF", exact=True)).to_be_enabled()
                cad_files = {}
                for fmt in ["dxf", "ifc"]:
                    with page.expect_download() as download:
                        click("导出 " + fmt.upper())
                    path = out / ("export." + fmt)
                    download.value.save_as(str(path))
                    cad_files[fmt] = path
                page.screenshot(path=str(out / "cad-export.png"), full_page=True)
            from app.services.cad_dxf import parse_dxf
            from app.services.cad_ifc import parse_ifc

            sys.path.insert(0, str(repo / "apps/api/tests"))
            from test_cad_formats import edited

            for fmt in ["dxf", "ifc"]:
                with case("cad_" + fmt + "_native_geometry_edit_preview_apply_reload"):
                    if fmt == "ifc":
                        # Fresh snapshot after DXF apply; preserve all CMS metadata.
                        with page.expect_download() as download:
                            click("导出 IFC")
                        download.value.save_as(str(cad_files[fmt]))
                    raw = cad_files[fmt].read_bytes()
                    parsed = (parse_dxf if fmt == "dxf" else parse_ifc)(raw)
                    rack = next(o for o in parsed["objects"] if o["kind"] == "rack")
                    pose = [3.0, 4.0, 0.2] if fmt == "dxf" else [4.0, 4.0, 0.25]
                    changed = out / ("native-edited." + fmt)
                    changed.write_bytes(
                        edited(raw, fmt, rack["id"], pose, 90 if fmt == "dxf" else -90)
                    )
                    with page.expect_response(
                        lambda r: r.request.method == "POST" and "/scene/cad/imports?" in r.url
                    ) as response:
                        page.get_by_label("导入 CAD 文件", exact=True).set_input_files(str(changed))
                    preview = response.value.json()
                    assert response.value.status == 201, preview
                    assert preview["can_apply"], preview
                    assert any(
                        d["id"] == rack["id"]
                        and d["status"] == "update"
                        and d["incoming"]["position"] == pose
                        for d in preview["diffs"]
                    )
                    expect(button("确认应用")).to_be_enabled()
                    page.locator("[data-cad-status=update] summary").click()
                    page.screenshot(path=str(out / (fmt + "-diff-preview.png")), full_page=True)
                    with page.expect_response(
                        lambda r: r.request.method == "POST" and r.url.endswith("/apply")
                    ) as applied:
                        click("确认应用")
                    assert applied.value.status == 200, applied.value.text()
                    expect(
                        page.get_by_text("此修订已应用；重复导入不会新增对象", exact=True)
                    ).to_be_visible()
                    with page.expect_response(
                        lambda r: r.request.method == "POST" and "/scene/cad/imports?" in r.url
                    ) as repeated:
                        page.get_by_label("导入 CAD 文件", exact=True).set_input_files(str(changed))
                    assert (
                        repeated.value.json()["applied"]
                        and repeated.value.json()["id"] == preview["id"]
                    )
                    click("关闭（未确认的差异不写入系统）")
                    with page.expect_response(
                        lambda r: "/api/v1/scene?" in r.url or r.url.endswith("/api/v1/scene")
                    ) as reloaded:
                        page.reload()
                    body = reloaded.value.json()
                    actual = next(r for r in body["racks"] if r["id"] == rack["id"])
                    assert [
                        actual["position_x"],
                        actual["position_y"],
                        actual["position_z"],
                    ] == pose
                    expect(
                        page.get_by_role("button", name="适配全部对象", exact=True)
                    ).to_be_enabled()
                    click("适配全部对象")
                    page.screenshot(path=str(out / (fmt + "-reloaded-3d.png")), full_page=True)
                    click("CAD 文件同步")
                    expect(page.get_by_role("button", name="导出 DXF", exact=True)).to_be_enabled()
            with case("cad_cancel_retains_draft_without_applying"):
                with page.expect_download() as download:
                    click("导出 DXF")
                fresh = out / "cancel-base.dxf"
                download.value.save_as(str(fresh))
                source = fresh.read_bytes()
                rack = next(o for o in parse_dxf(source)["objects"] if o["kind"] == "rack")
                change_a = out / "cancel-and-conflict-a.dxf"
                change_a.write_bytes(edited(source, "dxf", rack["id"], [5.0, 4.0, 0.25], 0))
                with page.expect_response(
                    lambda r: r.request.method == "POST" and "/scene/cad/imports?" in r.url
                ) as response:
                    page.get_by_label("导入 CAD 文件", exact=True).set_input_files(str(change_a))
                pending = response.value.json()
                assert pending["can_apply"], pending
                apply_count = sum(w["path"].endswith("/apply") for w in writes)
                click("关闭（未确认的差异不写入系统）")
                assert sum(w["path"].endswith("/apply") for w in writes) == apply_count
                click("CAD 文件同步")
                with page.expect_response(
                    lambda r: r.request.method == "POST" and "/scene/cad/imports?" in r.url
                ) as response:
                    page.get_by_label("导入 CAD 文件", exact=True).set_input_files(str(change_a))
                assert (
                    response.value.json()["id"] == pending["id"]
                    and not response.value.json()["applied"]
                )
            with case("cad_two_browser_stale_preview_conflict"):
                original = page
                second = browser.new_page(viewport={"width": 1800, "height": 1100})
                page = second
                page.goto(original.url)
                expect(button("新建")).to_be_enabled()
                click("CAD 文件同步")
                change_b = out / "conflict-b.dxf"
                change_b.write_bytes(edited(source, "dxf", rack["id"], [6.0, 4.0, 0.25], 0))
                page.get_by_label("导入 CAD 文件", exact=True).set_input_files(str(change_b))
                expect(button("确认应用")).to_be_enabled()
                with page.expect_response(
                    lambda r: r.request.method == "POST" and r.url.endswith("/apply")
                ) as response:
                    click("确认应用")
                assert response.value.status == 200, response.value.text()
                second.close()
                page = original
                with page.expect_response(
                    lambda r: r.request.method == "POST" and r.url.endswith("/apply")
                ) as response:
                    click("确认应用")
                assert response.value.status == 409
                expect(page.get_by_text("操作未完成", exact=True)).to_be_visible()
                click("重新核对差异")
                expect(button("确认应用")).to_be_disabled()
                expect(page.locator('[data-cad-status="conflict"]')).to_have_count(1)
                page.locator('[data-cad-status="conflict"] summary').click()
                page.screenshot(path=str(out / "cad-version-conflict.png"), full_page=True)
            with case("cad_small_screen_and_keyboard_cancel"):
                page.set_viewport_size({"width": 900, "height": 740})
                drawer = page.get_by_role("dialog")
                expect(drawer).to_be_visible()
                bounds = drawer.bounding_box()
                assert bounds["x"] >= 0 and bounds["width"] <= 900
                button("确认应用").scroll_into_view_if_needed()
                expect(button("确认应用")).to_be_in_viewport()
                page.screenshot(path=str(out / "cad-small-screen.png"), full_page=True)
                page.keyboard.press("Escape")
                expect(drawer).not_to_be_visible()
                page.set_viewport_size({"width": 1800, "height": 1100})
                click("CAD 文件同步")
            with case("cad_invalid_file_is_retained_but_blocked"):
                bad = out / "invalid.dxf"
                bad.write_text("not a DXF")
                page.get_by_label("导入 CAD 文件", exact=True).set_input_files(str(bad))
                expect(page.get_by_text("仅保存修订，不能应用", exact=True)).to_be_visible()
                expect(page.get_by_role("button", name="确认应用", exact=True)).to_be_disabled()
                page.screenshot(path=str(out / "cad-blocked-invalid.png"), full_page=True)
                click("关闭（未确认的差异不写入系统）")
            with case("browser_errors"):
                assert not errors, errors
            browser.close()
        server.should_exit = True
        thread.join(timeout=5)
except Exception:
    print(traceback.format_exc(), flush=True)
    sys.exit(1)
finally:
    print("Evidence: " + str(out), flush=True)
