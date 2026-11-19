"""parser 测试：每种语法一条 + 失败路径必须抛带行号的 DgmError。"""

import pytest

from dgm.parser import DgmError, parse_dgm

SAMPLE = """\
title: 订单状态机
subtitle: 一个订单只有一个状态
gap: 104,112

entry 下单成功 @1,1
key 待付款 @2,1 | 买家还没付款
end 已完成 @3,1
warn 已取消 @2,3 | 终态

group 交易主流程: 待付款, 已完成

下单成功 -> 待付款: 创建订单
待付款 -> 已取消: 超时 ~-40

legend 事件: entry
"""


def test_title_subtitle_gap():
    spec = parse_dgm(SAMPLE)
    assert spec["title"] == "订单状态机"
    assert spec["subtitle"] == "一个订单只有一个状态"
    assert spec["column_gap"] == 104
    assert spec["row_gap"] == 112


def test_gap_defaults():
    spec = parse_dgm("title: t\nentry a @1,1\n")
    assert spec["column_gap"] == 78
    assert spec["row_gap"] == 116


def test_node_types_position_desc():
    spec = parse_dgm(SAMPLE)
    by_id = {n["id"]: n for n in spec["nodes"]}
    assert by_id["下单成功"]["type"] == "entry"
    assert by_id["待付款"]["col"] == 2 and by_id["待付款"]["row"] == 1
    assert by_id["待付款"]["desc"] == "买家还没付款"
    assert by_id["已取消"]["type"] == "warn"
    assert by_id["已完成"]["desc"] == ""


def test_node_without_position_gets_auto_row():
    spec = parse_dgm("entry a @1,1\nb\n")
    b = spec["nodes"][1]
    assert (b["col"], b["row"]) == (1, 2)  # 跟在上一节点行的下一行


def test_group():
    spec = parse_dgm(SAMPLE)
    assert spec["groups"] == [{"label": "交易主流程", "nodes": ["待付款", "已完成"]}]


def test_edge_with_label_and_bend():
    spec = parse_dgm(SAMPLE)
    e1, e2 = spec["edges"]
    assert e1 == {"from": "下单成功", "to": "待付款", "label": "创建订单",
                  "bend": 0, "bidir": False}
    assert e2["bend"] == -40 and e2["label"] == "超时"


def test_bidir_edge():
    spec = parse_dgm("a @1,1\nb @2,1\na <-> b: 同步\n")
    (e,) = spec["edges"]
    assert e["bidir"] is True and e["label"] == "同步"


def test_legend():
    spec = parse_dgm(SAMPLE)
    assert spec["legend"] == [{"label": "事件", "type": "entry"}]


def test_comments_and_blank_lines_ignored():
    spec = parse_dgm("# 全是注释\n\nentry a @1,1  # 行尾注释\n")
    assert [n["id"] for n in spec["nodes"]] == ["a"]


def test_empty_spec():
    spec = parse_dgm("")
    assert spec["nodes"] == [] and spec["edges"] == []


# ---------------- 失败路径 ----------------


def test_error_edge_missing_arrow():
    with pytest.raises(DgmError) as ei:
        parse_dgm("entry a @1,1\nentry b @2,1\na b\n")
    assert '缺少 "->"' in str(ei.value)
    assert "第 3 行" in str(ei.value)


def test_error_at_not_number():
    with pytest.raises(DgmError) as ei:
        parse_dgm("entry a @x,1\n")
    assert '"@"' in str(ei.value)
    assert "第 1 行" in str(ei.value)


def test_error_position_extra_text():
    with pytest.raises(DgmError) as ei:
        parse_dgm("entry a @1,1 多余\n")
    assert "多余内容" in str(ei.value)


def test_error_edge_unknown_node():
    with pytest.raises(DgmError) as ei:
        parse_dgm("entry a @1,1\na -> 不存在: 去哪\n")
    assert '不存在的节点 "不存在"' in str(ei.value)


def test_error_group_unknown_member():
    with pytest.raises(DgmError) as ei:
        parse_dgm("entry a @1,1\ngroup g: a, 幽灵\n")
    assert '不存在的节点 "幽灵"' in str(ei.value)


def test_error_duplicate_node():
    with pytest.raises(DgmError) as ei:
        parse_dgm("entry a @1,1\nentry a @2,1\n")
    assert "重复定义" in str(ei.value)


def test_error_gap_not_int():
    with pytest.raises(DgmError) as ei:
        parse_dgm("gap: a,b\n")
    assert "两个整数" in str(ei.value)


def test_error_group_missing_colon():
    with pytest.raises(DgmError) as ei:
        parse_dgm("entry a @1,1\ngroup 只有名字\n")
    assert 'group 行缺少 ":"' in str(ei.value)


def test_error_legend_unknown_type():
    with pytest.raises(DgmError) as ei:
        parse_dgm("entry a @1,1\nlegend 图例: purple\n")
    assert "不认识" in str(ei.value)


def test_error_legend_missing_colon():
    with pytest.raises(DgmError) as ei:
        parse_dgm("legend 没有冒号\n")
    assert '缺少 ":"' in str(ei.value)


def test_error_typed_node_missing_name():
    with pytest.raises(DgmError) as ei:
        parse_dgm("entry | 只有描述\n")
    assert "缺少名字" in str(ei.value)
