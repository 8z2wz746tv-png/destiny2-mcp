"""刷取清单精确查询的离线契约。

清单是可选社区资料：查不到只代表本地清单没有这个名称，不能反推武器不值得刷。
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from destiny_mcp.services.starside_service import StarsideService
from destiny_mcp.tools.assistants import _farming_reference, _harvest_names


LIST_MARKDOWN = """# 刷取清单-测试

描述：测试用清单。
更新：2026.9.6

## 榴弹发射器

| 武器 | 图标 | 评级 | 框架\\\\射速 | 属性 | 勇士 | Perk 三号位 | Perk 四号位 | 获取地点 | 评级理由 |
|---|---|---|---|---|---|---|---|---|---|
| 迷失信号 | | T0 | 区域拒止\\\\72 | 冰影 | | 金中藏弹 | 爆破专家 | 安可 | 最好用的绿弹 |
| 翻新 A499 | | T2.5 | 干扰武器\\\\72 | 动能 | | 速射瞄准 | 聚合充能 | 无序边界 | 版本弃子 |
"""


def _service(tmp_path: Path, share_text: str = LIST_MARKDOWN) -> StarsideService:
    """随附 Markdown 版清单；归档目录留空。"""
    archive = tmp_path / "archive"
    archive.mkdir()
    share = tmp_path / "share"
    share.mkdir()
    (share / "legendary-special.md").write_text(share_text, encoding="utf-8")
    return StarsideService(None, archive, share_root=share)


def _archive_page(
    root: Path, page: str, title: str, headers: list[str], rows: list[list[str]]
) -> None:
    """写一份最小清单页记录：首列是武器名，其余是普通单元格。"""
    url = f"https://starside.work/{page}"
    record = {
        "url": url,
        "title": title,
        "category": "weapons",
        "updated_at": "2026.8.30",
        "text": title,
        "source_blocks": [],
        "tables": [
            {
                "caption": "",
                "heading": "",
                "rows": [
                    [{"tag": "th", "text": head, "html": head, "attrs": {}} for head in headers],
                    *[
                        [
                            {
                                "tag": "th" if index == 0 else "td",
                                "text": cell,
                                "html": cell,
                                "attrs": {},
                            }
                            for index, cell in enumerate(row)
                        ]
                        for row in rows
                    ],
                ],
            }
        ],
        "external_links": [],
        "archive": {
            "fetched_at": "2026-09-09T00:00:00Z",
            "sha256": sha256(url.encode()).hexdigest(),
        },
    }
    path = root / f"records/{page.removesuffix('/index.html')}/index.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")


def _archive_index(root: Path, *pages: str) -> None:
    """写只含指定页面的最小可用 schema v2 归档索引。"""
    (root / "exports").mkdir(exist_ok=True)
    (root / "exports/starsideIndex.json").write_text("[]", encoding="utf-8")
    (root / "exports/starsideDesc.json").write_text("{}", encoding="utf-8")
    (root / "index.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "provider": "starside",
                "status": "complete",
                "updated_at": "2026-09-09T05:57:18+00:00",
                "categories": {
                    "weapons": [
                        {
                            "url": f"https://starside.work/{page}",
                            "record": (
                                f"records/{page.removesuffix('/index.html')}/index.json"
                            ),
                            "source_blocks": 0,
                        }
                        for page in pages
                    ]
                },
                "failures": [],
                "pending_urls": [],
                "public_exports": [
                    "exports/starsideIndex.json",
                    "exports/starsideDesc.json",
                ],
                "crawler": {"pages_saved": len(pages), "pending": 0},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


SHOPPING_HEADERS = [
    "武器", "图标", "排名", "评级", "属性", "框架", "赛季", "来源", "勇士",
    "弹药生成", "枪管", "弹匣", "大师", "Perk 1", "Perk 2", "起源特性", "注解",
]


def test_lookup_returns_structured_row(tmp_path: Path) -> None:
    result = _service(tmp_path).lookup_farming("迷失信号")

    assert result["available"] is True
    assert result["matched_count"] == 1
    row = result["results"][0]
    assert row["name"] == "迷失信号"
    assert row["list"] == "刷取清单-绿弹紫枪"
    assert row["tier"] == "T0"
    assert row["frame"] == "区域拒止 72"
    assert row["element"] == "冰影"
    assert row["perks"] == {"三号位": ["金中藏弹"], "四号位": ["爆破专家"]}
    assert row["source"] == "安可"
    assert row["note"] == "最好用的绿弹"
    assert row["source_ref"]["source_type"] == "author_markdown"
    assert row["source_ref"]["updated_at"] == "2026.9.6"
    assert row["source_ref"]["trust"] == "untrusted_reference"


def test_lookup_is_exact_and_reports_unmatched(tmp_path: Path) -> None:
    """子串不再命中：这正是原来「问一把枪回来一堆」的根因。"""
    result = _service(tmp_path).lookup_farming(["迷失信号", "迷失", "不存在枪"])

    assert [row["name"] for row in result["results"]] == ["迷失信号"]
    assert result["unmatched"] == ["迷失", "不存在枪"]
    assert result["available"] is True


def test_lookup_is_case_insensitive_for_latin_names(tmp_path: Path) -> None:
    result = _service(tmp_path).lookup_farming("翻新 a499")

    assert result["matched_count"] == 1
    assert result["results"][0]["name"] == "翻新 A499"
    assert result["results"][0]["tier"] == "T2.5"


def test_lookup_deduplicates_and_respects_limit(tmp_path: Path) -> None:
    result = _service(tmp_path).lookup_farming(
        ["迷失信号", "迷失信号", "翻新 A499"], limit=1
    )

    assert result["matched_count"] == 2
    assert result["returned_count"] == 1
    assert result["truncated"] is True
    assert len(result["results"]) == 1


def test_missing_lists_report_unavailable(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    share = tmp_path / "share"
    share.mkdir()
    result = StarsideService(None, archive, share_root=share).lookup_farming("迷失信号")

    assert result["available"] is False
    assert result["matched_count"] == 0
    assert any("未安装" in warning for warning in result["warnings"])


def test_archive_only_list_resolves_by_url_key(tmp_path: Path) -> None:
    """异域武器清单只存在于归档，记录以完整 URL 为键而不是 page: 前缀。"""
    root = tmp_path / "archive"
    root.mkdir()
    url = "https://starside.work/exotic-weapons/index.html"
    headers = ("武器", "图标", "评级", "总伤", "DPS", "勇士", "理由一", "理由二", "理由三")
    record = {
        "url": url,
        "title": "刷取清单-异域武器",
        "category": "weapons",
        "updated_at": "2026.9.6",
        "text": "刷取清单-异域武器",
        "source_blocks": [],
        "tables": [
            {
                "caption": "",
                "heading": "",
                "rows": [
                    [
                        {"tag": "th", "text": head, "html": head, "attrs": {}}
                        for head in headers
                    ],
                    [
                        {"tag": "th", "text": "真相", "html": "真相", "attrs": {}},
                        {"tag": "td", "text": "", "html": "", "attrs": {}},
                        {
                            "tag": "td",
                            "text": "",
                            "html": "输出：T0<br>高难：T0.5",
                            "attrs": {},
                        },
                        {"tag": "td", "text": "119636", "html": "119636", "attrs": {}},
                        {"tag": "td", "text": "8973", "html": "8973", "attrs": {}},
                        {"tag": "td", "text": "", "html": "", "attrs": {}},
                        {"tag": "td", "text": "", "html": "一击删除八成场景", "attrs": {}},
                        {"tag": "td", "text": "", "html": "", "attrs": {}},
                        {"tag": "td", "text": "", "html": "", "attrs": {}},
                    ],
                    # 归档版的框架分组行：单个 th[scope=colgroup]，不是武器。
                    [
                        {
                            "tag": "th",
                            "text": "输出 T0：一键删除输出场景",
                            "html": "输出 T0：一键删除输出场景",
                            "attrs": {"colspan": "9", "scope": "colgroup"},
                        }
                    ],
                ],
            }
        ],
        "external_links": [],
        "archive": {
            "fetched_at": "2026-09-09T00:00:00Z",
            "sha256": sha256(url.encode()).hexdigest(),
        },
    }
    record_path = root / "records/exotic-weapons/index.json"
    record_path.parent.mkdir(parents=True)
    record_path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    _archive_index(root, "exotic-weapons/index.html")

    service = StarsideService(None, root, share_root=None)
    result = service.lookup_farming(["真相", "牵引器火炮"])

    assert result["available"] is True
    assert [row["name"] for row in result["results"]] == ["真相"]
    row = result["results"][0]
    assert row["list"] == "刷取清单-异域武器"
    assert row["scenario_tiers"] == {"输出": "T0", "高难": "T0.5"}
    assert "tier" not in row
    assert row["dps"] == "8973"
    assert row["source_ref"]["source_type"] == "web_archive_v2"
    assert list(service._farming_index()) == ["真相"]


def test_tier_list_is_a_labelled_fallback(tmp_path: Path) -> None:
    """精选刷取清单没有这把时，回退到购物清单的梯队，并带上自己的刻度与名次。"""
    root = tmp_path / "archive"
    root.mkdir()
    _archive_page(
        root,
        "shopping-heavy/index.html",
        "购物清单-威能",
        SHOPPING_HEADERS,
        [
            [
                "迎驾", "", "33", "D", "虚空", "精密", "13", "萨瓦拉", "", "45",
                "高速发射", "冲击弹壳", "操控性", "自动填装枪套",
                "持久印象<br>集束炸弹<br>小丑皇弹药筒", "先锋平反", "整体略差于巴尔米拉-B",
            ]
        ],
    )
    _archive_index(root, "shopping-heavy/index.html")

    result = StarsideService(None, root, share_root=None).lookup_farming("迎驾")

    assert result["lists"] == ["购物清单-威能"]
    row = result["results"][0]
    assert row["list"] == "购物清单-威能"
    assert row["scale"] == "S-F"
    assert row["tier"] == "D"
    assert row["rank"] == "33"
    assert row["source"] == "萨瓦拉"
    assert row["note"] == "整体略差于巴尔米拉-B"
    assert row["perks"] == {
        "三号位": ["自动填装枪套"],
        "四号位": ["持久印象", "集束炸弹", "小丑皇弹药筒"],
    }


def test_farm_list_wins_over_tier_list(tmp_path: Path) -> None:
    """两边都有：只返回精选刷取清单那一行，刻度是 T，不给出两套不可比的刻度。"""
    root = tmp_path / "archive"
    root.mkdir()
    _archive_page(
        root,
        "shopping-heavy/index.html",
        "购物清单-威能",
        SHOPPING_HEADERS,
        [
            [
                "翻新 A499", "", "1", "S", "动能", "干扰", "28", "反叛", "", "26",
                "槽化枪管", "广口弹匣", "填装", "速射瞄准", "聚合充能", "加速突击", "很强",
            ]
        ],
    )
    _archive_page(
        root,
        "legendary-heavy/index.html",
        "刷取清单-威能紫枪",
        [
            "武器", "图标", "评级", "框架", "属性", "勇士",
            "Perk 三号位", "Perk 四号位", "获取地点", "评级理由",
        ],
        [
            [
                "翻新 A499", "", "T0", "干扰武器 72", "动能", "",
                "速射瞄准", "聚合充能", "无序边界", "版本答案",
            ]
        ],
    )
    _archive_index(root, "shopping-heavy/index.html", "legendary-heavy/index.html")

    result = StarsideService(None, root, share_root=None).lookup_farming("翻新 A499")

    assert result["matched_count"] == 1
    assert result["lists"] == ["刷取清单-威能紫枪", "购物清单-威能"]
    row = result["results"][0]
    assert row["list"] == "刷取清单-威能紫枪"
    assert row["scale"] == "T"
    assert row["tier"] == "T0"


def test_tier_qualifier_is_kept_and_role_stays_separate(tmp_path: Path) -> None:
    """评级列混着档位和定位标签：档位带限定词要保留，定位标签不能当档位。"""
    marked = LIST_MARKDOWN.replace(
        "| 翻新 A499 | | T2.5 |",
        "| 旧版枪 | | T0（旧） | 区域拒止 72 | 烈日 | | 医治 | 互惠 | 扭曲 | 旧版本 |\n"
        "| 工具枪 | | 输出工具枪 | 区域拒止 72 | 虚空 | | 压制 | 爆破专家 | 苍白之心 | 工具定位 |\n"
        "| 翻新 A499 | | T2.5 |",
    )
    service = _service(tmp_path, share_text=marked)

    rows = {row["name"]: row for row in service.lookup_farming(["旧版枪", "工具枪"])["results"]}
    assert rows["旧版枪"]["tier"] == "T0（旧）"
    assert "role" not in rows["旧版枪"]
    assert rows["工具枪"]["role"] == "输出工具枪"
    assert "tier" not in rows["工具枪"]


def test_same_column_perks_stay_separate_alternatives(tmp_path: Path) -> None:
    """同一栏位换行分开的是备选，必须拆成列表，不能压成一个字符串。"""
    marked = LIST_MARKDOWN.replace(
        "| 金中藏弹 | 爆破专家 |",
        "| 金中藏弹 | 爆破专家\\\\我为人人\\\\高地 |",
    )
    service = _service(tmp_path, share_text=marked)

    row = service.lookup_farming("迷失信号")["results"][0]
    assert row["perks"] == {
        "三号位": ["金中藏弹"],
        "四号位": ["爆破专家", "我为人人", "高地"],
    }


def test_armor_list_uses_its_own_name_column_and_has_no_grade(tmp_path: Path) -> None:
    """护甲套装的名字列是「套装」，源数据没有档位列，不能编一个评级出来。"""
    root = tmp_path / "archive"
    root.mkdir()
    _archive_page(
        root,
        "farming-sets/index.html",
        "刷取清单-护甲套装",
        ["套装", "图标", "件数", "获取地点", "应用场景", "说明"],
        [
            [
                "埃希恩记忆", "", "4 件", "玻璃拱顶",
                "能量球 （输出 棱镜虚空软硬减）", "最泛用的套装",
            ]
        ],
    )
    _archive_index(root, "farming-sets/index.html")

    row = StarsideService(None, root, share_root=None).lookup_farming("埃希恩记忆")[
        "results"
    ][0]

    assert row["list"] == "刷取清单-护甲套装"
    assert row["scale"] == "ordered"
    assert "tier" not in row
    assert "role" not in row
    assert row["source"] == "玻璃拱顶"
    assert row["pieces"] == "4 件"
    assert row["scenario"] == "能量球 （输出 棱镜虚空软硬减）"
    assert row["note"] == "最泛用的套装"


def test_batched_sourcing_lookup_does_not_truncate(tmp_path: Path) -> None:
    """一次问很多名字时必须分批取全：截断会被读成「清单里没有」。"""
    generated = "\n".join(
        f"| 测试枪 {index} | | T0 | 轻质 600 | 动能 | | 医治 | 互惠 | 扭曲 | 测试 |"
        for index in range(1, 26)
    )
    marked = LIST_MARKDOWN.replace(
        "| 迷失信号 | | T0 | 区域拒止\\\\72 | 冰影 | | 金中藏弹 | 爆破专家 | 安可 | 最好用的绿弹 |",
        generated,
    )
    service = _service(tmp_path, share_text=marked)
    names = [f"测试枪 {index}" for index in range(1, 26)]

    result = service._lookup_sourcing(names)

    assert [row["name"] for row in result["results"]] == names
    assert service.lookup_farming(names, limit=20)["truncated"] is True


def test_group_rows_are_not_weapons(tmp_path: Path) -> None:
    """清单用 | == 框架评级 == | 分组，分隔行不能被当成武器名。"""
    marked = LIST_MARKDOWN.replace(
        "| 迷失信号 | | T0 |",
        "| == 区域拒止 T0：优秀的武器形态 == |\n| 迷失信号 | | T0 |",
    )
    service = _service(tmp_path, share_text=marked)

    service._load()
    assert [name for name in service._farming_index()] == ["迷失信号", "翻新 A499"]
    assert service.lookup_farming("迷失信号")["matched_count"] == 1


def test_edited_list_is_reloaded_without_restart(tmp_path: Path) -> None:
    """按原结构更新 share/ 内容后不需要重启，名单跟着变。"""
    service = _service(tmp_path)
    assert service.lookup_farming("迷失信号")["results"][0]["tier"] == "T0"
    assert service.lookup_farming("新枪")["matched_count"] == 0

    updated = LIST_MARKDOWN.replace("| 迷失信号 | | T0 |", "| 迷失信号 | | T1 |")
    updated += "| 新枪 | | T0 | 支援\\\\\\\\600 | 烈日 | | 医治 | 互惠 | 扭曲 | 新加的 |\n"
    (tmp_path / "share" / "legendary-special.md").write_text(updated, encoding="utf-8")

    row = service.lookup_farming("迷失信号")["results"][0]
    assert row["tier"] == "T1"
    added = service.lookup_farming("新枪")
    assert added["matched_count"] == 1
    assert added["results"][0]["note"] == "新加的"


def test_corrupt_markdown_degrades_instead_of_raising(tmp_path: Path) -> None:
    """社区资料损坏不能让官方数据查询失败。"""
    result = _farming_reference(_service(tmp_path, share_text="   \n"), "迷失信号")

    assert result["available"] is False
    assert result["matched_count"] == 0
    assert result["coverage_scope"] == "farming_list_unavailable"


def test_tool_helper_tolerates_missing_service() -> None:
    result = _farming_reference(None, "迷失信号")

    assert result["available"] is False
    assert result["matched_count"] == 0
    assert result["results"] == []


def test_harvest_names_skips_duplicates_and_stays_bounded() -> None:
    payload = {
        "weapons": [
            {"name": "真相", "item_hash": 1},
            {"name": "真相", "item_hash": 2},
            {"name": "牵引器火炮", "item_hash": 3},
            {"deep": {"name": "迷失信号", "item_hash": 4}},
        ]
    }

    assert _harvest_names(payload, limit=2) == ["真相", "牵引器火炮"]
    assert _harvest_names({"weapons": [{"item_hash": 1}, {"name": "  "}]}) == []
