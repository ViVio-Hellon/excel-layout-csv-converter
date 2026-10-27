r"""停止内訳(記号 → 内訳名)── **出どころを2つ持ち、どちらを読んだか言う**

【何に効くのか】
出すCSVのうち `停止内訳_<日付>_<ライン>.csv` には「内訳」の列があります。

    記号  内訳          分類
    イ    突発停止(機械)  段取り・突発停止
    0     休憩食事       管理ロス設備停止

記号と分類は**マスタが無くても出ます**(分類は記号の字種で決まる ── 数字は
管理ロス、片仮名は突発・待ち、英字はハンドリング)。読めないと空になるのは
**「内訳」の1列だけ**です。

【出どころは2つ。分類ごとに、CSVがあればCSV】
移行元(日報管理ツール)が v3.93.0 で変えた仕組みを、そのまま使います
(`nippou/logic/stop_csv.py` の `pick`):

    停止内訳.csv      … メモ帳で直せる。**管理者の鍵が要りません**
    伝送用ファイルの表 … 参照用マスタ(`作業停止時間内訳_1/_2/_3`)

**重ねません。** 重ねると「CSVから消した理由が表から戻ってくる」ので、
CSVを書き換えても一覧から消せなくなります。ただし分類(1/2/3)ごとなので、
CSVに `1` の行しか無ければ `2` と `3` は表のままです。

【置き場所が2つ要る理由】
参照用マスタは課の共有に1つ置いて全ラインが見ます。停止内訳CSVは現場で
直すものなので、**別の場所に置きたい**という話でした(利用者の言葉で
「パスも参照用マスタの置き場所とCSV置き場、2つ必要」)。設定部の
「停止内訳CSVの置き場所」が空なら、参照用マスタの置き場所の
`停止内訳.csv` を見ます(移行元の既定と同じ)。

【読めなくても変換は止めません】
停止理由が読めないことを理由に変換を止めると、**CSVが1本も出なくなります**。
読めなかったことと、その結果どこが空になるかを言って、先へ進みます。
"""
from __future__ import annotations

import csv
import unicodedata
from dataclasses import dataclass, field

#: CSVの書き方の見本。画面と `読み方.txt` に出します
SAMPLE = ("分類,内訳番号,内訳,備考", "1,0,休憩食事,", "2,イ,突発停止(機械),",
          "3,A,段取り変更,")

#: **名前(内訳)が空でよい記号**(分類1 の内訳番号)。日報管理ツール v4.44.13 の
#: `stop_csv.BLANK_LABEL_OK`(分類1・内訳番号99)と同じ決まりです。
#:
#: 写し(`vendor/`)は v3.96.0 でこの決まりを持たないので、**写しは直さずに**
#: ここで持ちます(VENDOR.md の約束)。写しを採り直したら、写しの
#: `stop_csv.blank_label_ok` を呼ぶだけに戻せます。
#:
#: 名前が空の 99 は**隠しません** ── 時間は今までどおり数え、名前の欄が空に
#: なるだけです。知らせも「記号のまま入れました」の代わりに件数と分を1行出します。
BLANK_NAME_CODES: frozenset[str] = frozenset({"99"})


def blank_name_ok(code: str) -> bool:
    """名前(内訳)が空でよい記号か。全角の `９９` も同じに見ます。"""
    return (unicodedata.normalize("NFKC", str(code or "")).strip()
            in BLANK_NAME_CODES)


def blank_name_row(text: str) -> bool:
    """停止内訳CSVの1行が `1,99,,` / `1,99`(名前の空でよい行)か。

    写しの `stop_csv.parse` はこの行を「内訳(理由の名前)がありません」
    (`1,99` だけなら「欄が足りません」)と言って捨てます。捨てても名前は
    空のままなので出すCSVは同じですが、**変換のたびに直せと言われる**のを
    止めます。
    """
    from nippou.logic import stop_csv

    delimiter = "," if "," in text or "\t" not in text else "\t"
    try:
        cells = [cell.strip() for cell in next(csv.reader([text], delimiter=delimiter))]
    except (csv.Error, StopIteration):
        return False
    if len(cells) < 2 or not blank_name_ok(cells[1]):
        return False
    if len(cells) > 2 and cells[2]:
        return False                              # 名前があるなら、ほかの理由
    return stop_csv.category_of(cells[0]) == stop_csv.CATEGORY_NAMES[0]


@dataclass
class Status:
    """停止内訳が読めたか。**画面・CLI・ログで同じものを使います。**"""

    ok: bool = False
    codes: int = 0                      # 使える記号の数
    csv_path: str = ""
    csv_exists: bool = False
    csv_error: str = ""
    master_path: str = ""
    #: 分類 → `CSV` / `マスタ` / ""(どちらにも無い)
    origins: dict[str, str] = field(default_factory=dict)
    #: CSVの直すところ(行番号つき)。**黙って捨てません**
    problems: list[str] = field(default_factory=list)

    def where(self) -> str:
        """分類ごとの出どころを1行で。"""
        return " / ".join(f"{name}={origin or '無し'}"
                          for name, origin in self.origins.items())

    def text(self) -> str:
        """人に出す1行(読めたときは出どころ、読めないときは影響まで)。"""
        if self.ok:
            found = f"停止内訳: {self.codes}記号({self.where()})"
            if self.csv_exists:
                found += f"／CSV: {self.csv_path}"
            if any(o == "マスタ" for o in self.origins.values()):
                found += f"／マスタ: {self.master_path}"
            return found + self._tail()
        return (f"停止内訳が読めません(CSV: {self.csv_path}"
                + (f" ── {self.csv_error}" if self.csv_error
                   else ("" if self.csv_exists else " ── ありません"))
                + f"／マスタ: {self.master_path})。"
                "停止内訳CSVの「内訳」欄が空になります(記号と分類は出ます)。"
                "設定部の「停止内訳CSVの置き場所」か「参照用マスタの置き場所」"
                "で指定できます" + self._tail())

    def _tail(self) -> str:
        if not self.problems:
            return ""
        return ("。停止内訳CSVに直すところがあります: "
                + " ／ ".join(self.problems[:4]))

    def as_dict(self) -> dict:
        return {"ok": self.ok, "codes": self.codes, "text": self.text(),
                "csv_path": self.csv_path, "csv_exists": self.csv_exists,
                "csv_error": self.csv_error, "master_path": self.master_path,
                "origins": dict(self.origins), "problems": list(self.problems)}


def read() -> Status:
    """いまの設定で停止内訳を読み、**どこから読めたか**を返す。

    表(伝送用マスタ)は**CSVに無い分類だけ**読みます(移行元の既定)──
    CSVが3分類とも持っていれば、共有の伝送用ファイルを写しに行きません。
    """
    from nippou.access_bridge import stop_master
    from nippou.config import SETTINGS

    status = Status(master_path=str(SETTINGS.stop_reason_master_path),
                    csv_path=str(SETTINGS.stop_reason_csv_path))
    try:
        found = stop_master.load_sources()
    except Exception as exc:                      # noqa: BLE001 - 変換は続ける
        status.csv_error = str(exc)
        return status

    status.csv_exists = bool(found.csv_exists)
    status.csv_error = str(found.csv_error or "")
    status.origins = dict(found.origins)
    # `1,99,,` は日報管理ツールの決まりで正しい行なので、直せとは言いません
    status.problems = [f"{p.line_no}行目: {p.reason}"
                       for p in found.csv.problems
                       if not blank_name_row(p.text)]
    codes = {reason.code for items in found.effective.values()
             for reason in items if reason.code}
    status.codes = len(codes)
    status.ok = bool(codes)
    return status


def codes() -> dict[str, str]:
    """記号 → 内訳名。**読めなければ空**(呼ぶ側は記号のまま出します)。

    機側データ集計が使います ── 変換が出した集計明細CSVには**記号しか
    入っていません**。同じ日の停止内訳CSVがあればそこから名前が引けますが、
    無いときや、内訳の列が空(変換したときにマスタが読めていなかった)
    ときの受け皿です。
    """
    try:
        from nippou.access_bridge import stop_master
        found = stop_master.load_sources()
    except Exception:                             # noqa: BLE001 - 無しで続ける
        return {}
    names: dict[str, str] = {}
    for items in found.effective.values():
        for reason in items:
            if reason.code and reason.label:
                names.setdefault(reason.code, reason.label)
    return names


def notice() -> tuple[bool, str]:
    """`(読めたか, 1行の知らせ)`。CLIと画面で同じ言葉を出すために。"""
    status = read()
    return status.ok, status.text()
