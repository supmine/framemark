"""Sony Exif Frame — desktop UI (PySide6). Rendering lives in render.py."""
from __future__ import annotations

import os
import sys
import traceback
from dataclasses import asdict, replace

from PIL import Image
from PySide6.QtCore import QObject, QRunnable, QSettings, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QSplitter, QVBoxLayout, QWidget,
)

import render

APP = "SonyExifFrame"


def pil_to_pixmap(im: Image.Image) -> QPixmap:
    im = im.convert("RGB")
    data = im.tobytes("raw", "RGB")
    q = QImage(data, im.width, im.height, im.width * 3, QImage.Format_RGB888)
    return QPixmap.fromImage(q.copy())


# ---------------------------------------------------------------- background jobs

class Signals(QObject):
    done = Signal(object, object)   # (token, result)
    failed = Signal(object, str)
    progress = Signal(int, str)


class Job(QRunnable):
    def __init__(self, fn, token=None):
        super().__init__()
        self.fn, self.token, self.sig = fn, token, Signals()

    def run(self):
        try:
            res = self.fn(self.sig)
            self.sig.done.emit(self.token, res)
        except Exception as e:
            traceback.print_exc()
            self.sig.failed.emit(self.token, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------- widgets

class Seg(QWidget):
    """Segmented button row; value() returns the selected key."""
    changed = Signal()

    def __init__(self, items, value):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.group = QButtonGroup(self)
        self.keys = []
        for i, (key, label) in enumerate(items):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setProperty("seg", "first" if i == 0 else "last" if i == len(items) - 1 else "mid")
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.group.addButton(b, i)
            self.keys.append(key)
            lay.addWidget(b)
        self.set_value(value)
        self.group.idClicked.connect(lambda _: self.changed.emit())

    def value(self):
        return self.keys[max(0, self.group.checkedId())]

    def set_value(self, v):
        i = self.keys.index(v) if v in self.keys else 0
        self.group.button(i).setChecked(True)


class Preview(QLabel):
    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(320, 320)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._pm = None
        self.setText("ลากภาพมาวางที่นี่ หรือกด “เพิ่มภาพ”\nรองรับ JPEG · PNG · WebP · TIFF · HEIC")
        self.setObjectName("preview")

    def set_pixmap(self, pm):
        self._pm = pm
        self._fit()

    def clear_pm(self, text):
        self._pm = None
        self.setPixmap(QPixmap())
        self.setText(text)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def _fit(self):
        if self._pm is None:
            return
        r = self.devicePixelRatioF()
        pm = self._pm.scaled(int(self.width() * r) - 40, int(self.height() * r) - 40, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        pm.setDevicePixelRatio(r)
        self.setPixmap(pm)


# ---------------------------------------------------------------- main window

class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sony Exif Frame")
        self.resize(1400, 900)
        self.setAcceptDrops(True)
        self.st = QSettings(APP, APP)
        self.pool = QThreadPool.globalInstance()
        self.photos: list[render.Photo] = []
        self.current: render.Photo | None = None
        self.render_token = 0
        self.saving = False
        self.logo_path = self.st.value("logo_path", render.DEFAULT_LOGO)
        if not os.path.exists(self.logo_path):
            self.logo_path = render.DEFAULT_LOGO
        self.logo_mask = None
        self._load_logo(self.logo_path, quiet=True)

        self.timer = QTimer(self, singleShot=True, interval=90)
        self.timer.timeout.connect(self._render_preview)

        self._build()
        self._restore()

    # ---------- UI ----------
    def _build(self):
        split = QSplitter()
        self.setCentralWidget(split)

        # left: photo list
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(12, 12, 6, 12)
        row = QHBoxLayout()
        add = QPushButton("เพิ่มภาพ…")
        add.clicked.connect(self._pick_files)
        rm = QPushButton("นำออก")
        rm.setProperty("ghost", True)
        rm.clicked.connect(self._remove_selected)
        row.addWidget(add)
        row.addWidget(rm)
        lv.addLayout(row)
        self.listw = QListWidget()
        self.listw.setIconSize(QSize(72, 72))
        self.listw.setSelectionMode(QListWidget.ExtendedSelection)
        self.listw.currentRowChanged.connect(self._select)
        lv.addWidget(self.listw)
        self.count_lbl = QLabel("0 ภาพ")
        self.count_lbl.setProperty("muted", True)
        lv.addWidget(self.count_lbl)
        left.setMinimumWidth(220)

        # center: preview
        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(6, 12, 6, 12)
        self.preview = Preview()
        cv.addWidget(self.preview, 1)
        self.info_lbl = QLabel("")
        self.info_lbl.setProperty("muted", True)
        self.info_lbl.setAlignment(Qt.AlignCenter)
        cv.addWidget(self.info_lbl)

        # right: options
        right_inner = QWidget()
        rv = QVBoxLayout(right_inner)
        rv.setContentsMargins(6, 12, 12, 12)
        rv.setSpacing(12)

        style = QGroupBox("รูปแบบ")
        sf = QFormLayout(style)
        self.seg_layout = Seg([("lr", "ค่า | โลโก้"), ("pro", "โลโก้ | ค่า"), ("center", "กลาง")], "lr")
        self.seg_shape = Seg([("orig", "ตามภาพ"), ("1:1", "1:1"), ("4:5", "4:5"), ("3:4", "3:4")], "4:5")
        self.seg_pad = Seg([("0.022", "บาง"), ("0.035", "ปกติ"), ("0.055", "หนา")], "0.035")
        self.seg_ts = Seg([("1", "เล็ก"), ("1.3", "กลาง"), ("1.65", "ใหญ่")], "1.3")
        sf.addRow("เลย์เอาต์", self.seg_layout)
        sf.addRow("สัดส่วน", self.seg_shape)
        sf.addRow("ขอบ", self.seg_pad)
        sf.addRow("ตัวอักษร", self.seg_ts)
        colors = QWidget()
        cg = QGridLayout(colors)
        cg.setContentsMargins(0, 0, 0, 0)
        cg.setSpacing(6)
        self.color_group = QButtonGroup(self)
        names = {"white": "ขาว", "ivory": "งาช้าง", "gray": "เทา", "sakura": "ซากุระ", "latte": "ลาเต้", "black": "ดำ"}
        self.color_keys = list(render.PALETTE)
        for i, key in enumerate(self.color_keys):
            b = QPushButton(names.get(key, key))
            b.setCheckable(True)
            bg = render.PALETTE[key]["bg"]
            fg = render.PALETTE[key]["main"]
            b.setStyleSheet(
                f"QPushButton{{background:rgb{bg};color:rgb{fg};border:1px solid #9aa3ab;border-radius:6px;padding:5px 4px}}"
                f"QPushButton:checked{{border:2px solid #2f8fbf}}"
            )
            self.color_group.addButton(b, i)
            cg.addWidget(b, i // 3, i % 3)
        self.color_group.button(0).setChecked(True)
        self.color_group.idClicked.connect(lambda _: self._changed())
        sf.addRow("สีกรอบ", colors)
        rv.addWidget(style)

        show = QGroupBox("แสดง")
        sg = QGridLayout(show)
        self.cb_date = QCheckBox("วันที่")
        self.cb_lens = QCheckBox("เลนส์")
        self.cb_focal = QCheckBox("ระยะเลนส์ (mm)")
        self.cb_eq = QCheckBox("เทียบ 35mm (APS-C)")
        self.cb_alpha = QCheckBox("ชื่อรุ่นแบบ α")
        self.cb_logo = QCheckBox("โลโก้ SONY")
        for i, cb in enumerate([self.cb_date, self.cb_lens, self.cb_focal, self.cb_eq, self.cb_alpha, self.cb_logo]):
            cb.setChecked(cb not in (self.cb_focal, self.cb_eq, self.cb_alpha))
            cb.toggled.connect(self._changed)
            sg.addWidget(cb, i // 2, i % 2)
        rv.addWidget(show)

        fields = QGroupBox("ข้อความของภาพนี้ (แก้ได้)")
        ff = QFormLayout(fields)
        self.ed = {}
        for key, label, ph in [
            ("model", "รุ่นกล้อง", "ILCE-6700"),
            ("lens", "เลนส์", "E 70-350mm F4.5-6.3 G OSS"),
            ("settings", "ค่ากล้อง", "อัตโนมัติจาก EXIF"),
            ("date", "วันที่", "15 May 2026 at 6:16 PM"),
        ]:
            e = QLineEdit()
            e.setPlaceholderText(ph)
            e.textEdited.connect(lambda txt, k=key: self._edit_field(k, txt))
            ff.addRow(label, e)
            self.ed[key] = e
        reset = QPushButton("คืนค่าจาก EXIF")
        reset.setProperty("ghost", True)
        reset.clicked.connect(self._reset_fields)
        ff.addRow("", reset)
        self.meta_warn = QLabel("")
        self.meta_warn.setWordWrap(True)
        self.meta_warn.setProperty("warn", True)
        ff.addRow(self.meta_warn)
        rv.addWidget(fields)

        logo = QGroupBox("โลโก้")
        lg = QHBoxLayout(logo)
        self.logo_thumb = QLabel()
        self.logo_thumb.setFixedSize(150, 36)
        self.logo_thumb.setAlignment(Qt.AlignCenter)
        self.logo_thumb.setStyleSheet("background:#fff;border:1px solid #9aa3ab;border-radius:5px")
        lb = QPushButton("เปลี่ยน…")
        lb.setProperty("ghost", True)
        lb.clicked.connect(self._pick_logo)
        lr = QPushButton("ค่าเริ่มต้น")
        lr.setProperty("ghost", True)
        lr.clicked.connect(lambda: self._load_logo(render.DEFAULT_LOGO))
        lg.addWidget(self.logo_thumb)
        lg.addWidget(lb)
        lg.addWidget(lr)
        lg.addStretch()
        rv.addWidget(logo)
        self._update_logo_thumb()

        out = QGroupBox("บันทึก")
        of = QFormLayout(out)
        self.seg_q = Seg([("jpg95", "JPEG 95"), ("jpg100", "JPEG 100"), ("png", "PNG")], "jpg95")
        self.seg_q.changed.connect(self._changed)
        of.addRow("ไฟล์", self.seg_q)
        self.cb_exif = QCheckBox("เก็บ EXIF เดิมไว้ในไฟล์ใหม่")
        self.cb_exif.setChecked(True)
        of.addRow("", self.cb_exif)
        orow = QHBoxLayout()
        self.out_lbl = QLabel()
        self.out_lbl.setProperty("muted", True)
        self.out_lbl.setWordWrap(True)
        ob = QPushButton("เลือก…")
        ob.setProperty("ghost", True)
        ob.clicked.connect(self._pick_out)
        orow.addWidget(self.out_lbl, 1)
        orow.addWidget(ob)
        of.addRow("โฟลเดอร์", orow)
        btns = QHBoxLayout()
        self.btn_one = QPushButton("บันทึกภาพนี้")
        self.btn_all = QPushButton("บันทึกทั้งหมด")
        self.btn_one.setProperty("primary", True)
        self.btn_all.setProperty("primary", True)
        self.btn_one.clicked.connect(lambda: self._save(only_current=True))
        self.btn_all.clicked.connect(lambda: self._save(only_current=False))
        btns.addWidget(self.btn_one)
        btns.addWidget(self.btn_all)
        of.addRow(btns)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        of.addRow(self.progress)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setProperty("muted", True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        of.addRow(self.status)
        rv.addWidget(out)
        rv.addStretch()

        for s in (self.seg_layout, self.seg_shape, self.seg_pad, self.seg_ts):
            s.changed.connect(self._changed)

        scroll = QScrollArea()
        scroll.setWidget(right_inner)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setMinimumWidth(400)

        split.addWidget(left)
        split.addWidget(center)
        split.addWidget(scroll)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 0)
        split.setSizes([240, 760, 420])

        open_act = QAction("Open", self, shortcut=QKeySequence.Open, triggered=self._pick_files)
        save_act = QAction("Save", self, shortcut=QKeySequence.Save, triggered=lambda: self._save(True))
        self.addAction(open_act)
        self.addAction(save_act)
        self._update_buttons()

    # ---------- options ----------
    def options(self) -> render.Options:
        return render.Options(
            shape=self.seg_shape.value(),
            layout=self.seg_layout.value(),
            color=self.color_keys[max(0, self.color_group.checkedId())],
            pad=float(self.seg_pad.value()),
            ts=float(self.seg_ts.value()),
            show_date=self.cb_date.isChecked(),
            show_lens=self.cb_lens.isChecked(),
            show_focal=self.cb_focal.isChecked(),
            show_eq=self.cb_eq.isChecked(),
            alpha_name=self.cb_alpha.isChecked(),
            use_logo=self.cb_logo.isChecked(),
            quality=self.seg_q.value(),
        )

    def _restore(self):
        o = self.st.value("options")
        if isinstance(o, dict):
            self.seg_shape.set_value(o.get("shape", "4:5"))
            self.seg_layout.set_value(o.get("layout", "lr"))
            self.seg_pad.set_value(str(o.get("pad", "0.035")))
            self.seg_ts.set_value(str(o.get("ts", "1.3")))
            self.seg_q.set_value(o.get("quality", "jpg95"))
            c = o.get("color", "white")
            if c in self.color_keys:
                self.color_group.button(self.color_keys.index(c)).setChecked(True)
            for cb, k in [(self.cb_date, "show_date"), (self.cb_lens, "show_lens"), (self.cb_focal, "show_focal"),
                          (self.cb_eq, "show_eq"), (self.cb_alpha, "alpha_name"), (self.cb_logo, "use_logo")]:
                if k in o:
                    cb.setChecked(str(o[k]).lower() in ("true", "1"))
        self.cb_exif.setChecked(str(self.st.value("keep_exif", "true")).lower() == "true")
        self.out_dir = self.st.value("out_dir", "") or ""
        self._update_out_lbl()

    def _persist(self):
        o = asdict(self.options())
        o["pad"] = self.seg_pad.value()
        o["ts"] = self.seg_ts.value()
        self.st.setValue("options", o)
        self.st.setValue("keep_exif", "true" if self.cb_exif.isChecked() else "false")
        self.st.setValue("out_dir", self.out_dir)
        self.st.setValue("logo_path", self.logo_path)

    def closeEvent(self, e):
        self._persist()
        super().closeEvent(e)

    def _changed(self, *_):
        self.timer.start()

    # ---------- files ----------
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = []
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if os.path.isdir(p):
                paths += [os.path.join(p, f) for f in sorted(os.listdir(p))]
            else:
                paths.append(p)
        self.add_paths(paths)

    def _pick_files(self):
        start = self.st.value("last_in", os.path.expanduser("~/Pictures"))
        files, _ = QFileDialog.getOpenFileNames(
            self, "เลือกภาพ", start, "Images (*.jpg *.jpeg *.png *.webp *.tif *.tiff *.heic *.heif *.JPG *.JPEG)")
        if files:
            self.st.setValue("last_in", os.path.dirname(files[0]))
            self.add_paths(files)

    def add_paths(self, paths):
        paths = [p for p in paths if p.lower().endswith(render.IMAGE_EXTS)]
        have = {p.path for p in self.photos}
        paths = [p for p in paths if p not in have]
        if not paths:
            return
        self.status.setText(f"กำลังเปิด {len(paths)} ภาพ…")

        def work(sig):
            out = []
            for i, p in enumerate(paths):
                try:
                    out.append(render.load_photo(p))
                except Exception as ex:
                    out.append((p, str(ex)))
                sig.progress.emit(i + 1, os.path.basename(p))
            return out

        job = Job(work)
        job.sig.done.connect(self._loaded)
        job.sig.failed.connect(lambda _, msg: self.status.setText("เปิดไฟล์ไม่สำเร็จ: " + msg))
        job.sig.progress.connect(lambda n, name: self.status.setText(f"กำลังเปิด {n}/{len(paths)} · {name}"))
        self.pool.start(job)

    def _loaded(self, _, results):
        bad = []
        for r in results:
            if isinstance(r, tuple):
                bad.append(os.path.basename(r[0]))
                continue
            r.orig_meta = replace(r.meta)
            self.photos.append(r)
            th = r.preview.copy()
            th.thumbnail((144, 144))
            it = QListWidgetItem(pil_to_pixmap(th), os.path.basename(r.path))
            if not r.meta.found:
                it.setToolTip("ไม่พบข้อมูลกล้องในไฟล์นี้")
                it.setText("⚠ " + it.text())
            self.listw.addItem(it)
        self.status.setText("เปิดไม่ได้: " + ", ".join(bad) if bad else "")
        if self.listw.currentRow() < 0 and self.photos:
            self.listw.setCurrentRow(0)
        self._update_buttons()

    def _remove_selected(self):
        rows = sorted({self.listw.row(i) for i in self.listw.selectedItems()}, reverse=True)
        for r in rows:
            self.listw.takeItem(r)
            self.photos.pop(r)
        if not self.photos:
            self.current = None
            self.preview.clear_pm("ลากภาพมาวางที่นี่ หรือกด “เพิ่มภาพ”")
            self.info_lbl.setText("")
            self._fill_fields()
        self._update_buttons()

    def _select(self, row):
        self.current = self.photos[row] if 0 <= row < len(self.photos) else None
        self._fill_fields()
        self._render_preview()

    # ---------- per-photo fields ----------
    def _fill_fields(self):
        p = self.current
        o = self.options()
        for k, e in self.ed.items():
            e.setEnabled(p is not None)
            if p is None:
                e.setText("")
            elif k == "settings":
                e.setText(p.meta.settings)
                e.setPlaceholderText(p.meta.settings_text(o))
            else:
                e.setText(getattr(p.meta, k))
        if p is not None and not p.meta.found:
            self.meta_warn.setText("ไฟล์นี้ไม่มีข้อมูลกล้อง ลอง export ใหม่โดยเปิด Metadata: All หรือพิมพ์ค่าเองด้านบน")
        else:
            self.meta_warn.setText("")

    def _edit_field(self, key, txt):
        if self.current is None:
            return
        setattr(self.current.meta, key, txt)
        self._changed()

    def _reset_fields(self):
        if self.current is None:
            return
        self.current.meta = replace(self.current.orig_meta)
        self._fill_fields()
        self._changed()

    # ---------- logo ----------
    def _load_logo(self, path, quiet=False):
        try:
            m = render.build_logo_mask(path)
        except Exception as e:
            m = None
            if not quiet:
                QMessageBox.warning(self, "โลโก้", f"เปิดไฟล์โลโก้ไม่ได้\n{e}")
        if m is None:
            if not quiet:
                QMessageBox.warning(self, "โลโก้", "ภาพนี้ดูเหมือนว่างเปล่า ลองใช้โลโก้สีดำบนพื้นขาวหรือพื้นโปร่งใส")
            return
        self.logo_mask, self.logo_path = m, path
        if hasattr(self, "logo_thumb"):
            self._update_logo_thumb()
            self._persist()
            self._changed()

    def _pick_logo(self):
        f, _ = QFileDialog.getOpenFileName(self, "เลือกไฟล์โลโก้", os.path.dirname(self.logo_path),
                                           "Images (*.png *.jpg *.jpeg *.webp)")
        if f:
            self._load_logo(f)

    def _update_logo_thumb(self):
        if self.logo_mask is None:
            self.logo_thumb.setText("ไม่มีโลโก้")
            return
        m = self.logo_mask.copy()
        m.thumbnail((260, 52), Image.LANCZOS)
        im = Image.new("RGB", m.size, (255, 255, 255))
        im.paste(Image.new("RGB", m.size, (0, 0, 0)), (0, 0), m)
        pm = pil_to_pixmap(im)
        pm.setDevicePixelRatio(2)
        self.logo_thumb.setPixmap(pm)

    # ---------- preview ----------
    def _render_preview(self):
        p = self.current
        if p is None:
            return
        self.render_token += 1
        token = self.render_token
        o = self.options()
        meta = replace(p.meta)
        mask = self.logo_mask
        W, H = render.output_size(p, o)
        k = min(1.0, 1800 / max(W, H))

        job = Job(lambda sig: render.draw_frame(p, meta, o, mask, k), token)
        job.sig.done.connect(self._preview_ready)
        job.sig.failed.connect(lambda _, msg: self.status.setText("พรีวิวผิดพลาด: " + msg))
        self.pool.start(job)
        self.info_lbl.setText(f"{os.path.basename(p.path)} · ไฟล์ที่ได้ {W}×{H} px")
        if not p.meta.settings:
            self.ed["settings"].setPlaceholderText(p.meta.settings_text(o))

    def _preview_ready(self, token, img):
        if token != self.render_token:
            return  # a newer render is on the way
        self.preview.set_pixmap(pil_to_pixmap(img))

    # ---------- saving ----------
    def _update_out_lbl(self):
        self.out_lbl.setText(self.out_dir or "โฟลเดอร์ “framed” ข้างภาพต้นฉบับ")

    def _pick_out(self):
        d = QFileDialog.getExistingDirectory(self, "เลือกโฟลเดอร์ปลายทาง", self.out_dir or os.path.expanduser("~/Pictures"))
        if d:
            self.out_dir = d
            self._update_out_lbl()
            self._persist()

    def _update_buttons(self):
        n = len(self.photos)
        self.count_lbl.setText(f"{n} ภาพ")
        self.btn_one.setEnabled(n > 0 and not self.saving)
        self.btn_all.setEnabled(n > 0 and not self.saving)

    def _save(self, only_current):
        if self.saving or not self.photos:
            return
        targets = [self.current] if only_current and self.current else list(self.photos)
        o = self.options()
        mask = self.logo_mask
        keep = self.cb_exif.isChecked()
        out_dir = self.out_dir
        jobs = [(p, replace(p.meta)) for p in targets]
        self._persist()
        self.saving = True
        self._update_buttons()
        self.progress.setRange(0, len(jobs))
        self.progress.setValue(0)
        self.progress.setVisible(True)

        def work(sig):
            saved, errors = [], []
            for i, (p, meta) in enumerate(jobs):
                dest_dir = out_dir or os.path.join(os.path.dirname(p.path), "framed")
                try:
                    saved.append(render.save_framed(p, meta, o, mask, dest_dir, keep_exif=keep))
                except Exception as ex:
                    errors.append(f"{os.path.basename(p.path)}: {ex}")
                sig.progress.emit(i + 1, os.path.basename(p.path))
            return saved, errors

        job = Job(work)
        job.sig.progress.connect(lambda n, name: (self.progress.setValue(n), self.status.setText(f"กำลังบันทึก {n}/{len(jobs)} · {name}")))
        job.sig.done.connect(self._saved)
        job.sig.failed.connect(lambda _, msg: self._saved(None, ([], [msg])))
        self.pool.start(job)

    def _saved(self, _, res):
        saved, errors = res
        self.saving = False
        self.progress.setVisible(False)
        self._update_buttons()
        msg = ""
        if saved:
            folder = os.path.dirname(saved[-1])
            msg = f"บันทึกแล้ว {len(saved)} ภาพ → {folder}"
        if errors:
            msg += ("\n" if msg else "") + "ไม่สำเร็จ: " + "; ".join(errors)
        self.status.setText(msg)


STYLE = """
QWidget { font-size: 13px; }
QGroupBox { font-weight: 600; border: 1px solid palette(mid); border-radius: 8px; margin-top: 10px; padding: 12px 10px 10px 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QLabel#preview { background: #1b1e21; color: #9aa3ab; border-radius: 10px; }
QLabel[muted="true"] { color: palette(placeholder-text); font-size: 12px; }
QLabel[warn="true"] { color: #b7791f; font-size: 12px; }
QPushButton { padding: 6px 12px; border-radius: 6px; border: 1px solid palette(mid); background: palette(button); }
QPushButton:checked { background: #2f8fbf; color: white; border-color: #2f8fbf; }
QPushButton[seg] { border-radius: 0; padding: 5px 8px; }
QPushButton[seg="first"] { border-top-left-radius: 6px; border-bottom-left-radius: 6px; }
QPushButton[seg="last"] { border-top-right-radius: 6px; border-bottom-right-radius: 6px; }
QPushButton[primary="true"] { background: #1f5e7a; color: white; border-color: #1f5e7a; font-weight: 600; padding: 8px 14px; }
QPushButton[primary="true"]:disabled { background: #7f9aa7; border-color: #7f9aa7; }
QLineEdit { padding: 5px 7px; border-radius: 5px; border: 1px solid palette(mid); font-family: Menlo, Consolas, monospace; }
QListWidget { border-radius: 8px; border: 1px solid palette(mid); }
QListWidget::item { padding: 4px; }
"""


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "render":
        # headless batch mode, same flags as render.py: Framemark render a.jpg b.jpg --shape 4:5
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        render._cli()
        return
    app = QApplication(sys.argv)
    app.setApplicationName("Sony Exif Frame")
    app.setStyleSheet(STYLE)
    w = Main()
    w.show()
    w.add_paths([a for a in sys.argv[1:] if os.path.isfile(a)])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
