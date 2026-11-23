r"""板集計 ── 日ごとのCSVを期間ぶんまとめて、グラフにして、書き出す

読み方は `converter.plate_source`、グラフは `converter.plate_graph`、
書き出しは `converter.plate_out` が持っています。ここは受けて渡すだけです。

【ラインの名前は一覧にあるものだけ受けます】
画面から来た字をそのままフォルダ名に使うと、`..\..` のような字で
**参照パスの外**を読ませられます。参照パス直下に実際にあるフォルダ名と
突き合わせて、無い名前は断ります ── 打ち間違いにも同じ断りが出ます。

【長くかかる仕事にはしません】
読むのはCSVだけで、ひと月ぶんでも100本ほどです(1日3本)。変換のように
進捗を見せる仕組みには載せず、その場で返します ── 代わりに日数の
上限(`plate_source.MAX_DAYS`)で歯止めを掛けてあります。
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from converter import (plate_graph, plate_lot, plate_out, plate_source,
                       report_db, user_settings)
from converter.logging_utils import get_logger

from . import Blueprint, Request, Response, json_response, refuse

log = get_logger("app.plate")

bp = Blueprint("plate")

#: 期間を指していないときに見せる既定(**直近1か月**)
DEFAULT_SPAN_DAYS = 30


def _base() -> tuple[Path | None, str]:
    """参照パス。`(場所, 困っていること)` ── 無ければ画面に出します。"""
    settings = user_settings.load()
    if not settings.plate_dir:
        return None, "板データ参照パスが未設定です(設定タブで指定してください)"
    try:
        base = user_settings.resolve(settings.plate_dir)
    except ValueError:
        return None, "板データ参照パスの書き方が読めません"
    if not base.is_dir():
        return None, f"板データ参照パスが見つかりません: {settings.plate_dir}"
    return base, ""


def _span(found: list[plate_source.LineFound]) -> dict[str, str]:
    """一覧から、**既定で見せる期間**を決める。

    データがあるのに「0件」と出る画面にしないため、**いちばん新しい日**を
    末尾にして、そこから1か月ぶんを初期値にします。
    """
    last = max((one.last for one in found if one.last), default="")
    if not last:
        today = date.today()
        return {"start": plate_source.date_text(today - timedelta(days=DEFAULT_SPAN_DAYS)),
                "end": plate_source.date_text(today)}
    end = plate_source.parse_date(last) or date.today()
    first = min((one.first for one in found if one.first), default="")
    start = end - timedelta(days=DEFAULT_SPAN_DAYS)
    earliest = plate_source.parse_date(first) if first else None
    if earliest and earliest > start:
        start = earliest
    return {"start": plate_source.date_text(start),
            "end": plate_source.date_text(end)}


@bp.get("/api/plate")
def read(request: Request) -> Response:
    """参照パスの様子(ラインの一覧と、見せる期間の既定)。"""
    base, problem = _base()
    if base is None:
        return json_response({"ok": False, "problem": problem, "lines": []})
    found = plate_source.lines(base)
    shared = _shared()
    read = report_db.state(shared) if shared else None
    return json_response({
        "ok": True, "base": str(base), "problem": "",
        "lines": [one.as_dict() for one in found],
        "span": _span(found),
        "max_days": plate_source.MAX_DAYS,
        # 共有の日報データ(ロットを探すの本命)。未設定なら `None`
        "shared": None if read is None else {
            "path": str(shared), "ok": read.ok, "reason": read.reason(),
            "lines": read.lines,
        },
    })


def _ask(request: Request) -> tuple[dict, Response | None]:
    """画面から来た指定を確かめる。`(使える指定, 断り)`。"""
    base, problem = _base()
    if base is None:
        return {}, refuse("板データを読めません", problem, status=400)
    body = request.json()
    names = [str(one).strip() for one in (body.get("lines") or []) if str(one).strip()]
    if not names:
        return {}, refuse("ラインが選ばれていません",
                          "読むラインを1つ以上選んでください", status=400)
    real = {one.name for one in plate_source.lines(base) if one.has_aggregate}
    unknown = [name for name in names if name not in real]
    if unknown:
        return {}, refuse("そのラインはありません",
                          "／".join(unknown) + " ── 一覧から選び直してください",
                          status=400)
    start = plate_source.parse_date(str(body.get("start") or ""))
    end = plate_source.parse_date(str(body.get("end") or ""))
    if start is None or end is None:
        return {}, refuse("期間が読めません",
                          "日付は 2026-11-01 のように指定してください", status=400)
    return {"base": base, "lines": names, "start": start, "end": end,
            "kind": str(body.get("kind") or "csv")}, None


def _collect(ask: dict) -> tuple[plate_source.Collected | None, Response | None]:
    try:
        collected = plate_source.collect(ask["base"], ask["lines"],
                                         ask["start"], ask["end"])
    except ValueError as exc:
        return None, refuse("期間が広すぎます", str(exc), status=400)
    except OSError as exc:
        return None, refuse("板データを読めませんでした", str(exc), status=500)
    return collected, None


def _note(ask: dict) -> str:
    start = plate_source.date_text(ask["start"])
    end = plate_source.date_text(ask["end"])
    span = start if start == end else f"{start} 〜 {end}"
    return f"{span} ／ {'・'.join(ask['lines'])}"


@bp.post("/api/plate/graph")
def graph(request: Request) -> Response:
    """期間ぶんまとめて、**グラフのタイル**を返す(書き出しはしません)。"""
    ask, denied = _ask(request)
    if denied is not None:
        return denied
    collected, failed = _collect(ask)
    if failed is not None:
        return failed
    board = plate_graph.build(collected, note=_note(ask))
    return json_response({"ok": True, "summary": collected.as_dict(),
                          **board})


@bp.post("/api/plate/export")
def export(request: Request) -> Response:
    r"""まとめたものを**出力先フォルダへ**書く(CSV か Excel)。

    **ダウンロードはしません。** 出す先は設定部の「出力先フォルダ」です。
    """
    ask, denied = _ask(request)
    if denied is not None:
        return denied
    if ask["kind"] not in ("csv", "xlsx"):
        return refuse("その形では出せません", ask["kind"], status=400)
    settings = user_settings.load()
    problems = user_settings.problems(settings, need_input=False)
    if problems:
        return refuse("出力先が決まっていません", "／".join(problems), status=400)
    collected, failed = _collect(ask)
    if failed is not None:
        return failed
    if collected.empty:
        return json_response({"ok": False,
                              "message": "その期間に読める集計がありませんでした",
                              "summary": collected.as_dict()})
    try:
        out_dir = user_settings.ensure_output(settings)
        written = plate_out.write(
            collected, out_dir, kind=ask["kind"],
            start=plate_source.date_text(ask["start"]),
            end=plate_source.date_text(ask["end"]))
    except OSError as exc:
        log.warning("板集計を書けませんでした: %s", exc)
        return refuse("書けませんでした", str(exc), status=500)
    log.info("板集計を書きました: %s", written["name"])
    return json_response({"ok": True, "written": written,
                          "summary": collected.as_dict(),
                          "message": f"{written['name']} を書きました"})


# ----------------------------------------------------------------------
# ロットを探す(日報管理ツールの「ロットを探す」の移植)
# ----------------------------------------------------------------------
def _lot_ask(request: Request) -> tuple[dict, Response | None]:
    """探す指定を確かめる。`(使える指定, 断り)`。

    ラインは**「全ライン」も受けます**(`*`)── 期間とロット№だけ分かって
    いて、どのラインか覚えていないことのほうが多いからです。
    """
    base, problem = _base()
    if base is None:
        return {}, refuse("板データを読めません", problem, status=400)
    body = request.json()
    line = str(body.get("line") or plate_lot.ALL_LINES).strip()
    chosen: list[str] = []
    if line and line != plate_lot.ALL_LINES:
        real = {one.name for one in plate_source.lines(base) if one.has_aggregate}
        if line not in real:
            return {}, refuse("そのラインはありません",
                              f"{line} ── 一覧から選び直してください", status=400)
        chosen = [line]
    start = plate_source.parse_date(str(body.get("start") or ""))
    end = plate_source.parse_date(str(body.get("end") or ""))
    if start is None:
        return {}, refuse("開始日が読めません",
                          "日付は 2026-11-01 のように指定してください", status=400)
    if end is None:
        return {}, refuse("終了日が読めません",
                          "日付は 2026-11-30 のように指定してください", status=400)
    return {"base": base, "lines": chosen, "start": start, "end": end,
            "lot": str(body.get("lot") or ""), "shared": _shared()}, None


def _shared() -> Path | None:
    """共有の日報データ(`日報データ.sqlite3`)。設定が空なら `None`。

    **読めるかどうかはここで決めません** ── 読めなかった理由は
    `converter.plate_lot` が画面に出します(探すのはやめません)。
    """
    text = user_settings.load().report_db
    if not text:
        return None
    try:
        return user_settings.resolve(text)
    except ValueError:
        return None


def _lot_search(ask: dict) -> tuple[plate_lot.Result | None, Response | None]:
    try:
        return plate_lot.search(ask["base"], ask["lines"], ask["start"],
                                ask["end"], ask["lot"],
                                shared=ask.get("shared")), None
    except plate_lot.BadRequest as exc:
        return None, refuse(str(exc), "", status=400)
    except OSError as exc:
        return None, refuse("探せませんでした", str(exc), status=500)


@bp.post("/api/plate/lots")
def lots(request: Request) -> Response:
    """期間とロット№で探す(**読むだけ**。何も書きません)。"""
    ask, denied = _lot_ask(request)
    if denied is not None:
        return denied
    result, failed = _lot_search(ask)
    if failed is not None:
        return failed
    return json_response({"ok": True, **result.as_dict()})


@bp.post("/api/plate/lots/export")
def lots_export(request: Request) -> Response:
    """当たった行を**出力先フォルダへ** CSV で書く(画面と同じ行)。"""
    ask, denied = _lot_ask(request)
    if denied is not None:
        return denied
    settings = user_settings.load()
    problems = user_settings.problems(settings, need_input=False)
    if problems:
        return refuse("出力先が決まっていません", "／".join(problems), status=400)
    result, failed = _lot_search(ask)
    if failed is not None:
        return failed
    if not result.rows:
        return json_response({"ok": False, **result.as_dict(),
                              "message": "当たった行がないので書きませんでした"})
    try:
        written = plate_lot.write_csv(result, user_settings.ensure_output(settings),
                                      start=ask["start"], end=ask["end"])
    except OSError as exc:
        log.warning("ロット検索のCSVを書けませんでした: %s", exc)
        return refuse("書けませんでした", str(exc), status=500)
    return json_response({"ok": True, **result.as_dict(), "written": written,
                          "message": f"{written['name']} を書きました"
                                     f"({written['rows']} 行)"})
