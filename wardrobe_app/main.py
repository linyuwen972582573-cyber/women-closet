from __future__ import annotations

import io
import os
import sqlite3
import uuid
import secrets
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
import urllib.request
import urllib.parse
import json

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image
from passlib.context import CryptContext
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "wardrobe.sqlite3"

UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(r["name"]) for r in rows}


LABEL_ZH = {
    "style": {
        "casual": "休闲",
        "formal": "正式",
        "streetwear": "街头",
        "vintage": "复古",
        "minimalist": "极简",
        "business": "商务",
        "sportswear": "运动",
        "unknown": "未知",
    },
    "category": {
        "t-shirt": "T恤",
        "shirt": "衬衫",
        "hoodie": "连帽衫/卫衣",
        "sweater": "毛衣",
        "jacket": "夹克",
        "coat": "大衣",
        "dress": "连衣裙",
        "skirt": "半身裙",
        "jeans": "牛仔裤",
        "pants": "长裤",
        "shorts": "短裤",
        "unknown": "未知",
    },
    "color": {
        "black": "黑色",
        "white": "白色",
        "gray": "灰色",
        "red": "红色",
        "orange": "橙色",
        "yellow": "黄色",
        "green": "绿色",
        "blue": "蓝色",
        "purple": "紫色",
        "brown": "棕色",
        "pink": "粉色",
        "beige": "米色",
        "navy": "藏青",
        "unknown": "未知",
    },
    "season": {
        "spring": "春季",
        "summer": "夏季",
        "autumn": "秋季",
        "winter": "冬季",
        "unknown": "未知",
    },
    "material": {
        "cotton": "棉",
        "wool": "羊毛",
        "denim": "牛仔",
        "leather": "皮革",
        "linen": "亚麻",
        "polyester": "聚酯",
        "silk": "丝绸",
        "knit": "针织",
        "unknown": "未知",
    },
    "silhouette": {
        "oversized": "宽松/廓形",
        "slim": "修身",
        "regular fit": "合身",
        "cropped": "短款",
        "longline": "长款",
        "a-line": "A字/伞摆",
        "straight": "直筒",
        "unknown": "未知",
    },
}


def label_zh(kind: str, label: str) -> str:
    return LABEL_ZH.get(kind, {}).get(label, label)


def top3_with_zh(kind: str, top3: list[dict[str, float]]) -> list[dict[str, float]]:
    out = []
    for x in top3 or []:
        out.append(
            {
                "label": x.get("label"),
                "label_zh": label_zh(kind, str(x.get("label"))),
                "score": float(x.get("score", 0.0)),
                "conf": float(x.get("conf", 0.0)),
            }
        )
    return out
_clip_color_labels = [
    "black",
    "white",
    "gray",
    "red",
    "orange",
    "yellow",
    "green",
    "blue",
    "purple",
    "brown",
    "pink",
    "beige",
    "navy",
]
_clip_season_labels = ["spring", "summer", "autumn", "winter"]
_clip_material_labels = [
    "cotton",
    "wool",
    "denim",
    "leather",
    "linen",
    "polyester",
    "silk",
    "knit",
]
_clip_silhouette_labels = [
    "oversized",
    "slim",
    "regular fit",
    "cropped",
    "longline",
    "a-line",
    "straight",
]


def _ensure_column(
    conn: sqlite3.Connection, *, table: str, column: str, ddl: str
) -> None:
    cols = _table_columns(conn, table)
    if column in cols:
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def db_init() -> None:
    with db_connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clothes (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER,
              name TEXT NOT NULL,
              category TEXT,
              season TEXT,
              material TEXT,
              silhouette TEXT,
              style TEXT,
              color TEXT,
              price REAL,
              brand TEXT,
              notes TEXT,
              image_path TEXT,
              embedding BLOB,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              username TEXT NOT NULL UNIQUE,
              password_hash TEXT NOT NULL,
              role TEXT NOT NULL DEFAULT 'user',
              reset_token TEXT,
              reset_expires_at TEXT,
              created_at TEXT NOT NULL
            );
            """
        )
        # Migration safety for existing DBs created before these columns existed
        _ensure_column(conn, table="users", column="role", ddl="role TEXT NOT NULL DEFAULT 'user'")
        _ensure_column(conn, table="users", column="reset_token", ddl="reset_token TEXT")
        _ensure_column(conn, table="users", column="reset_expires_at", ddl="reset_expires_at TEXT")
        _ensure_column(conn, table="clothes", column="user_id", ddl="user_id INTEGER")
        _ensure_column(conn, table="clothes", column="category", ddl="category TEXT")
        _ensure_column(conn, table="clothes", column="season", ddl="season TEXT")
        _ensure_column(conn, table="clothes", column="material", ddl="material TEXT")
        _ensure_column(conn, table="clothes", column="silhouette", ddl="silhouette TEXT")

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_clothes_created_at
            ON clothes(created_at);
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_clothes_user_id
            ON clothes(user_id);
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_users_username
            ON users(username);
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_users_reset_token
            ON users(reset_token);
            """
        )


# ---------------------------
# Image analysis (MVP)
# - dominant color name
# - zero-shot style label via CLIP (optional, heavy)
# - image embedding for similarity (CLIP)
# ---------------------------


@dataclass(frozen=True)
class AnalyzeResult:
    color_name: str
    style_label: str
    category_label: str
    season_label: str
    material_label: str
    silhouette_label: str
    color_top3: list[dict[str, float]]
    style_top3: list[dict[str, float]]
    category_top3: list[dict[str, float]]
    season_top3: list[dict[str, float]]
    material_top3: list[dict[str, float]]
    silhouette_top3: list[dict[str, float]]
    embedding: Optional[np.ndarray]  # shape (d,), float32


_clip_model = None
_clip_style_labels = [
    "casual",
    "formal",
    "streetwear",
    "vintage",
    "minimalist",
    "business",
    "sportswear",
]
_clip_category_labels = [
    "t-shirt",
    "shirt",
    "hoodie",
    "sweater",
    "jacket",
    "coat",
    "dress",
    "skirt",
    "jeans",
    "pants",
    "shorts",
]


def _lazy_load_clip():
    global _clip_model
    if _clip_model is not None:
        return _clip_model
    if os.environ.get("WARDROBE_DISABLE_CLIP", "").strip() == "1":
        _clip_model = None
        return None
    try:
        from sentence_transformers import SentenceTransformer
    except Exception:  # pragma: no cover
        _clip_model = None
        return None
    # CLIP image-text model. Downloads on first run.
    _clip_model = SentenceTransformer("clip-ViT-B-32")
    return _clip_model


def _dominant_color_name(img: Image.Image) -> str:
    # Heuristic to avoid background dominating:
    # - focus on center crop (clothes usually centered)
    # - ignore very dark pixels (common for studio/room background)
    rgb_img = img.convert("RGB")
    w, h = rgb_img.size
    if w <= 0 or h <= 0:
        return "unknown"

    # Center crop (70% of width/height)
    cw = int(w * 0.7)
    ch = int(h * 0.7)
    left = max(0, (w - cw) // 2)
    top = max(0, (h - ch) // 2)
    crop = rgb_img.crop((left, top, left + cw, top + ch))

    crop.thumbnail((128, 128))
    arr = np.asarray(crop, dtype=np.uint8).reshape(-1, 3)
    if arr.size == 0:
        return "unknown"

    # Brightness proxy (0..255)
    v = arr.max(axis=1).astype(np.int16)
    # Keep pixels that are not too dark (background)
    keep = v >= 45
    kept = arr[keep]
    if kept.size == 0:
        kept = arr  # fallback

    # Robust "dominant" color:
    # - compute mean on filtered pixels
    # - also look at the brighter subset to avoid logos/shadows pulling "white" into gray
    mean = kept.mean(axis=0)
    r, g, b = [float(x) for x in mean]

    v_kept = kept.max(axis=1).astype(np.float32)
    try:
        thr = float(np.percentile(v_kept, 85))
    except Exception:
        thr = float(v_kept.max()) if v_kept.size else 0.0
    bright = kept[v_kept >= thr] if v_kept.size else kept
    if bright.size:
        br, bg, bb = [float(x) for x in bright.mean(axis=0)]
    else:
        br, bg, bb = r, g, b

    # Basic HSV-like heuristics (without extra deps)
    mx = max(br, bg, bb)
    mn = min(br, bg, bb)
    chroma = mx - mn
    # "Value" ~ mx, "Saturation" ~ chroma/mx
    sat = (chroma / mx) if mx > 1e-6 else 0.0

    # If very bright and low saturation -> white
    if mx >= 190 and sat <= 0.22:
        return "white"
    # If mid-bright and low saturation -> gray
    if 90 <= mx <= 210 and sat <= 0.15:
        return "gray"
    # If very dark -> black
    if mx <= 60:
        return "black"

    r_i, g_i, b_i = int(r), int(g), int(b)

    # Simple nearest color name mapping.
    palette = {
        "black": (0, 0, 0),
        "white": (255, 255, 255),
        "gray": (128, 128, 128),
        "red": (220, 20, 60),
        "orange": (255, 140, 0),
        "yellow": (255, 215, 0),
        "green": (34, 139, 34),
        "blue": (30, 144, 255),
        "purple": (138, 43, 226),
        "brown": (139, 69, 19),
        "pink": (255, 105, 180),
        "beige": (245, 245, 220),
        "navy": (0, 0, 128),
    }

    def dist2(a, c):
        return (a[0] - c[0]) ** 2 + (a[1] - c[1]) ** 2 + (a[2] - c[2]) ** 2

    best = min(palette.items(), key=lambda kv: dist2((r_i, g_i, b_i), kv[1]))[0]
    return best


def analyze_image(file_bytes: bytes) -> AnalyzeResult:
    img = Image.open(io.BytesIO(file_bytes))
    color_rule = _dominant_color_name(img)

    model = _lazy_load_clip()
    if model is None:
        return AnalyzeResult(
            color_name=color_rule,
            style_label="unknown",
            category_label="unknown",
            season_label="unknown",
            material_label="unknown",
            silhouette_label="unknown",
            color_top3=[],
            style_top3=[],
            category_top3=[],
            season_top3=[],
            material_top3=[],
            silhouette_top3=[],
            embedding=None,
        )

    # SentenceTransformer CLIP supports encoding images and text.
    try:
        rgb = img.convert("RGB")
        w, h = rgb.size

        def crop_center(scale: float) -> Image.Image:
            cw = max(1, int(w * scale))
            ch = max(1, int(h * scale))
            left = max(0, (w - cw) // 2)
            top = max(0, (h - ch) // 2)
            return rgb.crop((left, top, left + cw, top + ch))

        def crop_upper_body() -> Image.Image:
            # Heuristic: keep upper ~65% where tops/jackets usually are
            return rgb.crop((0, 0, w, max(1, int(h * 0.65))))

        crops = [
            rgb,  # full
            crop_center(0.8),
            crop_center(0.6),
            crop_upper_body(),
        ]

        style_text = model.encode(
            _clip_style_labels, convert_to_numpy=True, normalize_embeddings=True
        )
        cat_text = model.encode(
            _clip_category_labels, convert_to_numpy=True, normalize_embeddings=True
        )
        color_text = model.encode(
            _clip_color_labels, convert_to_numpy=True, normalize_embeddings=True
        )
        season_text = model.encode(
            _clip_season_labels, convert_to_numpy=True, normalize_embeddings=True
        )
        material_text = model.encode(
            _clip_material_labels, convert_to_numpy=True, normalize_embeddings=True
        )
        silhouette_text = model.encode(
            _clip_silhouette_labels, convert_to_numpy=True, normalize_embeddings=True
        )
        season_text = model.encode(
            _clip_season_labels, convert_to_numpy=True, normalize_embeddings=True
        )
        material_text = model.encode(
            _clip_material_labels, convert_to_numpy=True, normalize_embeddings=True
        )
        silhouette_text = model.encode(
            _clip_silhouette_labels, convert_to_numpy=True, normalize_embeddings=True
        )

        style_scores = np.zeros((len(_clip_style_labels),), dtype=np.float32)
        cat_scores = np.zeros((len(_clip_category_labels),), dtype=np.float32)
        color_scores = np.zeros((len(_clip_color_labels),), dtype=np.float32)
        season_scores = np.zeros((len(_clip_season_labels),), dtype=np.float32)
        material_scores = np.zeros((len(_clip_material_labels),), dtype=np.float32)
        silhouette_scores = np.zeros((len(_clip_silhouette_labels),), dtype=np.float32)
        img_embs = []
        for c in crops:
            emb = model.encode(c, convert_to_numpy=True, normalize_embeddings=True).astype(
                np.float32
            )
            img_embs.append(emb)
            style_scores += (style_text @ emb).astype(np.float32).reshape(-1)
            cat_scores += (cat_text @ emb).astype(np.float32).reshape(-1)
            color_scores += (color_text @ emb).astype(np.float32).reshape(-1)
            season_scores += (season_text @ emb).astype(np.float32).reshape(-1)
            material_scores += (material_text @ emb).astype(np.float32).reshape(-1)
            silhouette_scores += (silhouette_text @ emb).astype(np.float32).reshape(-1)

        style_scores /= float(len(crops))
        cat_scores /= float(len(crops))
        color_scores /= float(len(crops))
        season_scores /= float(len(crops))
        material_scores /= float(len(crops))
        silhouette_scores /= float(len(crops))

        def top3(labels: list[str], scores: np.ndarray) -> list[dict[str, float]]:
            idx = np.argsort(scores)[::-1][:3]
            top = scores[idx]
            # softmax over top3 for "confidence"
            m = float(np.max(top)) if top.size else 0.0
            exps = np.exp(top - m) if top.size else np.array([], dtype=np.float32)
            denom = float(exps.sum()) if exps.size else 1.0
            out = []
            for i, s, e in zip(idx.tolist(), top.tolist(), exps.tolist()):
                out.append({"label": labels[int(i)], "score": float(s), "conf": float(e / denom)})
            return out

        style_top = top3(_clip_style_labels, style_scores)
        cat_top = top3(_clip_category_labels, cat_scores)
        color_top = top3(_clip_color_labels, color_scores)
        season_top = top3(_clip_season_labels, season_scores)
        material_top = top3(_clip_material_labels, material_scores)
        silhouette_top = top3(_clip_silhouette_labels, silhouette_scores)

        style = style_top[0]["label"] if style_top else "unknown"
        category = cat_top[0]["label"] if cat_top else "unknown"
        color_clip = color_top[0]["label"] if color_top else "unknown"
        season = season_top[0]["label"] if season_top else "unknown"
        material = material_top[0]["label"] if material_top else "unknown"
        silhouette = silhouette_top[0]["label"] if silhouette_top else "unknown"

        # Choose final color:
        # Prefer CLIP top1 when available (semantic color works better on outdoor/background-heavy photos).
        # Fallback to rule-based color when CLIP is unavailable/empty.
        color = color_clip if color_top else color_rule

        # Use full-image embedding for downstream similarity (stable)
        image_emb = img_embs[0] if img_embs else None
        return AnalyzeResult(
            color_name=color,
            style_label=style,
            category_label=category,
            season_label=season,
            material_label=material,
            silhouette_label=silhouette,
            color_top3=color_top,
            style_top3=style_top,
            category_top3=cat_top,
            season_top3=season_top,
            material_top3=material_top,
            silhouette_top3=silhouette_top,
            embedding=image_emb.astype(np.float32) if image_emb is not None else None,
        )
    except Exception:
        return AnalyzeResult(
            color_name=color_rule,
            style_label="unknown",
            category_label="unknown",
            season_label="unknown",
            material_label="unknown",
            silhouette_label="unknown",
            color_top3=[],
            style_top3=[],
            category_top3=[],
            season_top3=[],
            material_top3=[],
            silhouette_top3=[],
            embedding=None,
        )


def embedding_to_blob(emb: Optional[np.ndarray]) -> Optional[bytes]:
    if emb is None:
        return None
    arr = np.asarray(emb, dtype=np.float32).reshape(-1)
    return arr.tobytes()


def blob_to_embedding(blob: Optional[bytes]) -> Optional[np.ndarray]:
    if blob is None:
        return None
    arr = np.frombuffer(blob, dtype=np.float32)
    if arr.size == 0:
        return None
    return arr


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    # if stored normalized, dot is cosine; still safe.
    a = np.asarray(a, dtype=np.float32).reshape(-1)
    b = np.asarray(b, dtype=np.float32).reshape(-1)
    if a.size == 0 or b.size == 0 or a.size != b.size:
        return 0.0
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a, b) / denom)


def safe_float(s: Optional[str]) -> Optional[float]:
    if s is None:
        return None
    s = str(s).strip()
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def normalize_price_similarity(a: Optional[float], b: Optional[float]) -> float:
    if a is None or b is None:
        return 0.0
    if a <= 0 or b <= 0:
        return 0.0
    ratio = min(a, b) / max(a, b)
    return float(ratio)


def field_match(a: Optional[str], b: Optional[str]) -> float:
    if not a or not b:
        return 0.0
    return 1.0 if a.strip().lower() == b.strip().lower() else 0.0


def compute_score(
    *,
    query: dict[str, Any],
    candidate: dict[str, Any],
    weights: dict[str, float],
    image_sim: float,
) -> dict[str, Any]:
    w_img = float(weights.get("image", 0.55))
    w_category = float(weights.get("category", 0.10))
    w_style = float(weights.get("style", 0.2))
    w_color = float(weights.get("color", 0.15))
    w_brand = float(weights.get("brand", 0.05))
    w_price = float(weights.get("price", 0.05))

    category_sim = field_match(query.get("category"), candidate.get("category"))
    style_sim = field_match(query.get("style"), candidate.get("style"))
    color_sim = field_match(query.get("color"), candidate.get("color"))
    brand_sim = field_match(query.get("brand"), candidate.get("brand"))
    price_sim = normalize_price_similarity(
        safe_float(query.get("price")), safe_float(candidate.get("price"))
    )

    # Weighted score in [0, 1]
    total_w = max(1e-9, w_img + w_category + w_style + w_color + w_brand + w_price)
    score = (
        w_img * image_sim
        + w_category * category_sim
        + w_style * style_sim
        + w_color * color_sim
        + w_brand * brand_sim
        + w_price * price_sim
    ) / total_w

    return {
        "score": float(max(0.0, min(1.0, score))),
        "components": {
            "image": float(image_sim),
            "category": float(category_sim),
            "style": float(style_sim),
            "color": float(color_sim),
            "brand": float(brand_sim),
            "price": float(price_sim),
        },
        "weights": {
            "image": w_img,
            "category": w_category,
            "style": w_style,
            "color": w_color,
            "brand": w_brand,
            "price": w_price,
        },
    }


app = FastAPI(title="Wardrobe App")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("WARDROBE_SECRET_KEY", "dev-secret-key-change-me"),
    session_cookie="wardrobe_session",
)


class TrialGateMiddleware(BaseHTTPMiddleware):
    """
    Simple shared passcode gate for demo.
    Set env WARDROBE_TRIAL_CODE to enable.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        code = os.environ.get("WARDROBE_TRIAL_CODE", "").strip()
        if not code:
            return await call_next(request)

        path = request.url.path
        allow_prefixes = (
            "/static",
            "/data",
            "/openapi.json",
            "/docs",
            "/redoc",
            "/trial",
            "/healthz",
        )
        if any(path == p or path.startswith(p + "/") for p in allow_prefixes):
            return await call_next(request)

        if request.session.get("trial_ok") == True:
            return await call_next(request)

        return RedirectResponse("/trial", status_code=303)


app.add_middleware(TrialGateMiddleware)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["label_zh"] = label_zh
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.mount("/data", StaticFiles(directory=str(DATA_DIR)), name="data")

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def hash_password(raw: str) -> str:
    return pwd_context.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(raw, hashed)
    except Exception:
        return False


def get_current_user(request: Request) -> Optional[sqlite3.Row]:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return row


def require_user(user: Optional[sqlite3.Row] = Depends(get_current_user)) -> sqlite3.Row:
    if user is None:
        raise HTTPException(status_code=302, headers={"Location": "/auth/login"})
    return user


def require_admin(user: sqlite3.Row = Depends(require_user)) -> sqlite3.Row:
    if (user.get("role") if hasattr(user, "get") else user["role"]) != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user


@app.on_event("startup")
def _on_startup():
    db_init()


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/trial", response_class=HTMLResponse)
def trial_form(request: Request):
    return templates.TemplateResponse("trial.html", {"request": request, "error": None})


@app.post("/trial")
def trial_submit(request: Request, code: str = Form(...)):
    expected = os.environ.get("WARDROBE_TRIAL_CODE", "").strip()
    if not expected:
        request.session["trial_ok"] = True
        return RedirectResponse("/", status_code=303)
    if (code or "").strip() != expected:
        return templates.TemplateResponse(
            "trial.html", {"request": request, "error": "口令不正确。"}, status_code=400
        )
    request.session["trial_ok"] = True
    return RedirectResponse("/", status_code=303)


@app.get("/", response_class=HTMLResponse)
def home(request: Request, user: Optional[sqlite3.Row] = Depends(get_current_user)):
    with db_connect() as conn:
        if user is None:
            rows = []
        else:
            rows = conn.execute(
                "SELECT * FROM clothes WHERE user_id = ? ORDER BY datetime(created_at) DESC, id DESC",
                (user["id"],),
            ).fetchall()
    return templates.TemplateResponse(
        "index.html", {"request": request, "clothes": rows, "user": user}
    )


@app.get("/add", response_class=HTMLResponse)
def add_form(request: Request, user: sqlite3.Row = Depends(require_user)):
    return templates.TemplateResponse("add.html", {"request": request, "user": user})


@app.post("/api/analyze-image")
def api_analyze_image(
    user: sqlite3.Row = Depends(require_user),
    image: UploadFile = File(...),
):
    if not image.filename:
        raise HTTPException(status_code=400, detail="No file")
    file_bytes = image.file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Empty file")
    analyzed = analyze_image(file_bytes)
    return JSONResponse(
        {
            "color": analyzed.color_name,
            "color_zh": label_zh("color", analyzed.color_name),
            "style": analyzed.style_label,
            "style_zh": label_zh("style", analyzed.style_label),
            "category": analyzed.category_label,
            "category_zh": label_zh("category", analyzed.category_label),
            "season": analyzed.season_label,
            "season_zh": label_zh("season", analyzed.season_label),
            "material": analyzed.material_label,
            "material_zh": label_zh("material", analyzed.material_label),
            "silhouette": analyzed.silhouette_label,
            "silhouette_zh": label_zh("silhouette", analyzed.silhouette_label),
            "color_top3": analyzed.color_top3,
            "color_top3_zh": top3_with_zh("color", analyzed.color_top3),
            "style_top3": analyzed.style_top3,
            "style_top3_zh": top3_with_zh("style", analyzed.style_top3),
            "category_top3": analyzed.category_top3,
            "category_top3_zh": top3_with_zh("category", analyzed.category_top3),
            "season_top3": analyzed.season_top3,
            "season_top3_zh": top3_with_zh("season", analyzed.season_top3),
            "material_top3": analyzed.material_top3,
            "material_top3_zh": top3_with_zh("material", analyzed.material_top3),
            "silhouette_top3": analyzed.silhouette_top3,
            "silhouette_top3_zh": top3_with_zh("silhouette", analyzed.silhouette_top3),
        }
    )


@app.get("/outfit", response_class=HTMLResponse)
def outfit_form(request: Request, user: sqlite3.Row = Depends(require_user)):
    return templates.TemplateResponse(
        "outfit.html",
        {
            "request": request,
            "user": user,
            "result": None,
        },
    )


def _group_category(cat: str) -> str:
    cat = (cat or "").strip().lower()
    tops = {"t-shirt", "shirt", "hoodie", "sweater"}
    outers = {"jacket", "coat"}
    bottoms = {"pants", "jeans", "shorts", "skirt"}
    if cat in tops:
        return "top"
    if cat in outers:
        return "outer"
    if cat in bottoms:
        return "bottom"
    if cat == "dress":
        return "dress"
    return "other"


def _basic_outfit_suggestions(rows: list[sqlite3.Row], prefs: dict[str, str]) -> list[dict[str, Any]]:
    # Very small heuristic engine: pick combinations by category groups.
    by_group: dict[str, list[sqlite3.Row]] = {"outer": [], "top": [], "bottom": [], "dress": [], "other": []}
    for r in rows:
        by_group[_group_category(r["category"] or "")].append(r)

    def score_item(r: sqlite3.Row) -> float:
        s = 0.0
        want_style = (prefs.get("style") or "").strip().lower()
        want_color = (prefs.get("color") or "").strip().lower()
        if want_style and (r["style"] or "").strip().lower() == want_style:
            s += 1.0
        if want_color and (r["color"] or "").strip().lower() == want_color:
            s += 0.7
        return s

    for g in by_group:
        by_group[g].sort(key=score_item, reverse=True)

    suggestions = []
    # Outfit type 1: outer + top + bottom
    if by_group["top"] and by_group["bottom"]:
        outer = by_group["outer"][0] if by_group["outer"] else None
        top = by_group["top"][0]
        bottom = by_group["bottom"][0]
        items = [x for x in [outer, top, bottom] if x is not None]
        suggestions.append(
            {
                "title": "推荐搭配 1",
                "items": items,
                "reason": "根据你的风格/颜色偏好，从上装/下装（可选外套）里挑选了最匹配的组合。",
            }
        )
    # Outfit type 2: dress (+ outer)
    if by_group["dress"]:
        outer = by_group["outer"][0] if by_group["outer"] else None
        dress = by_group["dress"][0]
        items = [x for x in [outer, dress] if x is not None]
        suggestions.append(
            {
                "title": "推荐搭配 2",
                "items": items,
                "reason": "连衣裙方案更省心，外套可根据温度增减。",
            }
        )
    return suggestions[:3]


def _llm_outfit_advice(wardrobe: list[dict[str, Any]], prefs: dict[str, str]) -> Optional[str]:
    url = os.environ.get("WARDROBE_LLM_URL", "").strip()
    if not url:
        return None
    api_key = os.environ.get("WARDROBE_LLM_API_KEY", "").strip()
    payload = {
        "wardrobe": wardrobe,
        "prefs": prefs,
        "lang": "zh-CN",
        "task": "outfit_advice",
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", "ignore")
            obj = json.loads(raw)
            return obj.get("advice") or obj.get("text")
    except Exception:
        return None


@app.post("/outfit", response_class=HTMLResponse)
def outfit_submit(
    request: Request,
    user: sqlite3.Row = Depends(require_user),
    occasion: str = Form(""),
    style: str = Form(""),
    color: str = Form(""),
    weather: str = Form(""),
    notes: str = Form(""),
):
    prefs = {
        "occasion": occasion.strip(),
        "style": style.strip(),
        "color": color.strip(),
        "weather": weather.strip(),
        "notes": notes.strip(),
    }
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT * FROM clothes WHERE user_id = ? ORDER BY datetime(created_at) DESC, id DESC",
            (user["id"],),
        ).fetchall()

    suggestions = _basic_outfit_suggestions(rows, prefs)
    wardrobe_for_llm = [
        {
            "id": r["id"],
            "name": r["name"],
            "category": r["category"] or "",
            "style": r["style"] or "",
            "color": r["color"] or "",
            "brand": r["brand"] or "",
            "price": r["price"],
            "notes": r["notes"] or "",
        }
        for r in rows
    ]
    llm_text = _llm_outfit_advice(wardrobe_for_llm, prefs)

    return templates.TemplateResponse(
        "outfit.html",
        {
            "request": request,
            "user": user,
            "result": {
                "prefs": prefs,
                "suggestions": suggestions,
                "llm_text": llm_text,
            },
        },
    )


def _save_upload(upload: UploadFile) -> tuple[str, bytes]:
    if not upload.filename:
        raise HTTPException(status_code=400, detail="No file name")
    contents = upload.file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Empty file")
    ext = os.path.splitext(upload.filename)[1].lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        # Allow most images but keep common ext names.
        ext = ".jpg"
    fname = f"{uuid.uuid4().hex}{ext}"
    path = UPLOADS_DIR / fname
    path.write_bytes(contents)
    return str(path.relative_to(BASE_DIR)), contents


@app.post("/add")
def add_item(
    request: Request,
    user: sqlite3.Row = Depends(require_user),
    name: str = Form(""),
    category: str = Form(""),
    season: str = Form(""),
    material: str = Form(""),
    silhouette: str = Form(""),
    style: str = Form(""),
    color: str = Form(""),
    price: str = Form(""),
    brand: str = Form(""),
    notes: str = Form(""),
    image: Optional[UploadFile] = File(None),
):
    image_path = None
    emb_blob = None
    style_auto = None
    color_auto = None

    if image is not None and image.filename:
        rel_path, file_bytes = _save_upload(image)
        image_path = rel_path
        analyzed = analyze_image(file_bytes)
        emb_blob = embedding_to_blob(analyzed.embedding)
        style_auto = analyzed.style_label
        color_auto = analyzed.color_name
        category_auto = analyzed.category_label
        season_auto = analyzed.season_label
        material_auto = analyzed.material_label
        silhouette_auto = analyzed.silhouette_label
    else:
        raise HTTPException(status_code=400, detail="必须上传图片")

    category_final = (category.strip() or (category_auto or "")).strip()
    season_final = (season.strip() or (season_auto or "")).strip()
    material_final = (material.strip() or (material_auto or "")).strip()
    silhouette_final = (silhouette.strip() or (silhouette_auto or "")).strip()
    style_final = (style.strip() or (style_auto or "")).strip()
    color_final = (color.strip() or (color_auto or "")).strip()
    price_f = safe_float(price)
    name_final = name.strip()
    if not name_final:
        # 自动生成一个可读名称（用户之后可编辑）
        parts = [p for p in [color_final, category_final] if p]
        name_final = " ".join(parts) if parts else "未命名衣服"

    now = utc_now_iso()
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO clothes(user_id, name, category, season, material, silhouette, style, color, price, brand, notes, image_path, embedding, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user["id"],
                name_final,
                category_final or None,
                season_final or None,
                material_final or None,
                silhouette_final or None,
                style_final or None,
                color_final or None,
                price_f,
                brand.strip() or None,
                notes.strip() or None,
                image_path,
                emb_blob,
                now,
                now,
            ),
        )
    return RedirectResponse("/", status_code=303)


@app.get("/edit/{item_id}", response_class=HTMLResponse)
def edit_form(
    request: Request,
    item_id: int,
    user: sqlite3.Row = Depends(require_user),
):
    with db_connect() as conn:
        row = conn.execute(
            "SELECT * FROM clothes WHERE id = ? AND user_id = ?", (item_id, user["id"])
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    return templates.TemplateResponse(
        "edit.html", {"request": request, "item": row, "user": user}
    )


@app.post("/edit/{item_id}")
def edit_item(
    request: Request,
    item_id: int,
    user: sqlite3.Row = Depends(require_user),
    name: str = Form(...),
    category: str = Form(""),
    season: str = Form(""),
    material: str = Form(""),
    silhouette: str = Form(""),
    style: str = Form(""),
    color: str = Form(""),
    price: str = Form(""),
    brand: str = Form(""),
    notes: str = Form(""),
    image: Optional[UploadFile] = File(None),
    reanalyze: str = Form(""),
):
    with db_connect() as conn:
        row = conn.execute(
            "SELECT * FROM clothes WHERE id = ? AND user_id = ?",
            (item_id, user["id"]),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Not found")

        image_path = row["image_path"]
        emb_blob = row["embedding"]
        style_auto = None
        color_auto = None
        category_auto = None
        season_auto = None
        material_auto = None
        silhouette_auto = None

        if image is not None and image.filename:
            rel_path, file_bytes = _save_upload(image)
            image_path = rel_path
            analyzed = analyze_image(file_bytes)
            emb_blob = embedding_to_blob(analyzed.embedding)
            style_auto = analyzed.style_label
            color_auto = analyzed.color_name
            category_auto = analyzed.category_label
            season_auto = analyzed.season_label
            material_auto = analyzed.material_label
            silhouette_auto = analyzed.silhouette_label
        elif reanalyze == "1" and image_path:
            # Reanalyze existing image file
            file_bytes = (BASE_DIR / image_path).read_bytes()
            analyzed = analyze_image(file_bytes)
            emb_blob = embedding_to_blob(analyzed.embedding)
            style_auto = analyzed.style_label
            color_auto = analyzed.color_name
            category_auto = analyzed.category_label
            season_auto = analyzed.season_label
            material_auto = analyzed.material_label
            silhouette_auto = analyzed.silhouette_label

        category_final = (category.strip() or (category_auto or "")).strip()
        season_final = (season.strip() or (season_auto or "")).strip()
        material_final = (material.strip() or (material_auto or "")).strip()
        silhouette_final = (silhouette.strip() or (silhouette_auto or "")).strip()
        style_final = (style.strip() or (style_auto or "")).strip()
        color_final = (color.strip() or (color_auto or "")).strip()
        price_f = safe_float(price)

        conn.execute(
            """
            UPDATE clothes
            SET name=?, category=?, season=?, material=?, silhouette=?, style=?, color=?, price=?, brand=?, notes=?, image_path=?, embedding=?, updated_at=?
            WHERE id=?
            """,
            (
                name.strip(),
                category_final or None,
                season_final or None,
                material_final or None,
                silhouette_final or None,
                style_final or None,
                color_final or None,
                price_f,
                brand.strip() or None,
                notes.strip() or None,
                image_path,
                emb_blob,
                utc_now_iso(),
                item_id,
            ),
        )
    return RedirectResponse("/", status_code=303)


@app.post("/delete/{item_id}")
def delete_item(item_id: int, user: sqlite3.Row = Depends(require_user)):
    with db_connect() as conn:
        row = conn.execute(
            "SELECT * FROM clothes WHERE id = ? AND user_id = ?",
            (item_id, user["id"]),
        ).fetchone()
        if row is None:
            return RedirectResponse("/", status_code=303)
        if row["image_path"]:
            try:
                (BASE_DIR / row["image_path"]).unlink(missing_ok=True)
            except Exception:
                pass
        conn.execute("DELETE FROM clothes WHERE id = ?", (item_id,))
    return RedirectResponse("/", status_code=303)


@app.get("/compare", response_class=HTMLResponse)
def compare_form(request: Request, user: sqlite3.Row = Depends(require_user)):
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT id, name FROM clothes WHERE user_id = ? ORDER BY id DESC",
            (user["id"],),
        ).fetchall()
    return templates.TemplateResponse(
        "compare.html", {"request": request, "clothes": rows, "result": None, "user": user}
    )


@app.post("/compare", response_class=HTMLResponse)
def compare_submit(
    request: Request,
    user: sqlite3.Row = Depends(require_user),
    target_id: int = Form(...),
    image: UploadFile = File(...),
    # Optional user overrides for query fields
    category: str = Form(""),
    style: str = Form(""),
    color: str = Form(""),
    price: str = Form(""),
    brand: str = Form(""),
    # Weights
    w_image: float = Form(0.55),
    w_category: float = Form(0.10),
    w_style: float = Form(0.2),
    w_color: float = Form(0.15),
    w_brand: float = Form(0.05),
    w_price: float = Form(0.05),
):
    rel_path, file_bytes = _save_upload(image)
    analyzed = analyze_image(file_bytes)
    query_emb = analyzed.embedding

    with db_connect() as conn:
        target = conn.execute(
            "SELECT * FROM clothes WHERE id=? AND user_id = ?", (target_id, user["id"])
        ).fetchone()
        rows = conn.execute(
            "SELECT * FROM clothes WHERE user_id = ?", (user["id"],)
        ).fetchall()
    if target is None:
        raise HTTPException(status_code=404, detail="Target not found")

    target_emb = blob_to_embedding(target["embedding"])
    image_sim_target = (
        cosine_similarity(query_emb, target_emb)
        if (query_emb is not None and target_emb is not None)
        else 0.0
    )

    # Query fields: user input > auto from image > blank
    query_fields = {
        "category": (category.strip() or analyzed.category_label or ""),
        "style": (style.strip() or analyzed.style_label or ""),
        "color": (color.strip() or analyzed.color_name or ""),
        "price": safe_float(price),
        "brand": brand.strip(),
    }

    weights = {
        "image": float(w_image),
        "category": float(w_category),
        "style": float(w_style),
        "color": float(w_color),
        "brand": float(w_brand),
        "price": float(w_price),
    }

    def row_to_fields(r: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": r["id"],
            "name": r["name"],
            "category": r["category"] or "",
            "style": r["style"] or "",
            "color": r["color"] or "",
            "price": r["price"],
            "brand": r["brand"] or "",
            "image_path": r["image_path"],
        }

    scored = []
    for r in rows:
        cand = row_to_fields(r)
        cand_emb = blob_to_embedding(r["embedding"])
        image_sim = (
            cosine_similarity(query_emb, cand_emb)
            if (query_emb is not None and cand_emb is not None)
            else 0.0
        )
        score_info = compute_score(
            query=query_fields, candidate=cand, weights=weights, image_sim=image_sim
        )
        scored.append(
            {
                "candidate": cand,
                "score": score_info["score"],
                "components": score_info["components"],
            }
        )
    scored.sort(key=lambda x: x["score"], reverse=True)

    with db_connect() as conn:
        list_rows = conn.execute(
            "SELECT id, name FROM clothes WHERE user_id = ? ORDER BY id DESC",
            (user["id"],),
        ).fetchall()

    result = {
        "upload_path": rel_path,
        "auto_category": analyzed.category_label,
        "auto_style": analyzed.style_label,
        "auto_color": analyzed.color_name,
        "target": row_to_fields(target),
        "target_image_similarity": float(image_sim_target),
        "query_fields": query_fields,
        "weights": weights,
        "top_matches": scored[:10],
    }

    return templates.TemplateResponse(
        "compare.html", {"request": request, "clothes": list_rows, "result": result, "user": user}
    )


# ---------------------------
# Auth: register / login / logout
# ---------------------------


@app.get("/auth/register", response_class=HTMLResponse)
def register_form(request: Request, user: Optional[sqlite3.Row] = Depends(get_current_user)):
    if user is not None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("register.html", {"request": request, "user": None})


@app.post("/auth/register")
def register_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    username = username.strip()
    if not username or not password:
        raise HTTPException(status_code=400, detail="用户名和密码不能为空")
    if len(password) < 4:
        raise HTTPException(status_code=400, detail="密码至少 4 位")

    password_hash = hash_password(password)
    with db_connect() as conn:
        existing_users = conn.execute("SELECT COUNT(1) AS c FROM users").fetchone()["c"]
    role = "admin" if int(existing_users) == 0 else "user"
    with db_connect() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO users(username, password_hash, role, created_at) VALUES(?, ?, ?, ?)",
                (username, password_hash, role, utc_now_iso()),
            )
            user_id = cur.lastrowid
        except sqlite3.IntegrityError:
            # 用户名已存在
            return templates.TemplateResponse(
                "register.html",
                {
                    "request": request,
                    "user": None,
                    "error": "用户名已存在，请换一个。",
                },
                status_code=400,
            )

    request.session["user_id"] = user_id
    return RedirectResponse("/", status_code=303)


@app.get("/auth/login", response_class=HTMLResponse)
def login_form(request: Request, user: Optional[sqlite3.Row] = Depends(get_current_user)):
    if user is not None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "user": None})


@app.post("/auth/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    with db_connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username.strip(),)
        ).fetchone()
    if row is None or not verify_password(password, row["password_hash"]):
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "user": None,
                "error": "用户名或密码错误。",
            },
            status_code=400,
        )

    request.session["user_id"] = row["id"]
    return RedirectResponse("/", status_code=303)


@app.post("/auth/logout")
def logout(request: Request):
    request.session.clear()
    resp = RedirectResponse("/", status_code=303)
    return resp


@app.get("/auth/forgot", response_class=HTMLResponse)
def forgot_form(request: Request, user: Optional[sqlite3.Row] = Depends(get_current_user)):
    if user is not None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("forgot.html", {"request": request, "user": None})


@app.post("/auth/forgot", response_class=HTMLResponse)
def forgot_submit(request: Request, username: str = Form(...)):
    username = username.strip()
    token = secrets.token_urlsafe(24)
    expires_at = datetime.now(timezone.utc).replace(microsecond=0)
    # default expiry: 30 minutes
    expires_at = expires_at.replace(second=expires_at.second)  # no-op but keeps seconds
    expires_iso = (datetime.now(timezone.utc).replace(microsecond=0)).isoformat(timespec="seconds")
    # store: token + expiry = now+30m
    exp = datetime.now(timezone.utc).replace(microsecond=0)
    exp = exp + (datetime.now(timezone.utc) - datetime.now(timezone.utc))  # placeholder to avoid extra import
    # Manually add 30 minutes without importing timedelta (keep minimal deps)
    exp_ts = int(datetime.now(timezone.utc).timestamp()) + 30 * 60
    exp_iso = datetime.fromtimestamp(exp_ts, tz=timezone.utc).isoformat(timespec="seconds")

    with db_connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if row is not None:
            conn.execute(
                "UPDATE users SET reset_token=?, reset_expires_at=? WHERE id=?",
                (token, exp_iso, row["id"]),
            )

    # 为了避免“用户名是否存在”的信息泄露：即使不存在也显示同样提示。
    reset_link = f"/auth/reset?token={token}"
    return templates.TemplateResponse(
        "forgot.html",
        {
            "request": request,
            "user": None,
            "message": "如果该用户名存在，我们已生成重置方式（开发版会直接显示链接）。",
            "reset_link": reset_link,
        },
    )


@app.get("/auth/reset", response_class=HTMLResponse)
def reset_form(request: Request, token: str = ""):
    token = (token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Missing token")
    return templates.TemplateResponse(
        "reset.html", {"request": request, "user": None, "token": token}
    )


@app.post("/auth/reset", response_class=HTMLResponse)
def reset_submit(request: Request, token: str = Form(...), new_password: str = Form(...)):
    token = token.strip()
    if len(new_password) < 4:
        return templates.TemplateResponse(
            "reset.html",
            {
                "request": request,
                "user": None,
                "token": token,
                "error": "新密码至少 4 位。",
            },
            status_code=400,
        )
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE reset_token = ?", (token,)).fetchone()
        if row is None:
            return templates.TemplateResponse(
                "reset.html",
                {"request": request, "user": None, "token": token, "error": "链接无效或已过期。"},
                status_code=400,
            )
        exp = row["reset_expires_at"]
        if exp:
            try:
                exp_ts = int(datetime.fromisoformat(exp.replace("Z", "+00:00")).timestamp())
                if int(datetime.now(timezone.utc).timestamp()) > exp_ts:
                    return templates.TemplateResponse(
                        "reset.html",
                        {"request": request, "user": None, "token": token, "error": "链接已过期。"},
                        status_code=400,
                    )
            except Exception:
                pass

        conn.execute(
            "UPDATE users SET password_hash=?, reset_token=NULL, reset_expires_at=NULL WHERE id=?",
            (hash_password(new_password), row["id"]),
        )
        request.session["user_id"] = row["id"]
    return RedirectResponse("/", status_code=303)


@app.get("/account", response_class=HTMLResponse)
def account_page(request: Request, user: sqlite3.Row = Depends(require_user)):
    return templates.TemplateResponse(
        "account.html", {"request": request, "user": user}
    )


@app.post("/account/password", response_class=HTMLResponse)
def change_password(
    request: Request,
    user: sqlite3.Row = Depends(require_user),
    old_password: str = Form(...),
    new_password: str = Form(...),
):
    if len(new_password) < 4:
        return templates.TemplateResponse(
            "account.html",
            {"request": request, "user": user, "error": "新密码至少 4 位。"},
            status_code=400,
        )
    with db_connect() as conn:
        fresh = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
    if fresh is None or not verify_password(old_password, fresh["password_hash"]):
        return templates.TemplateResponse(
            "account.html",
            {"request": request, "user": user, "error": "旧密码不正确。"},
            status_code=400,
        )
    with db_connect() as conn:
        conn.execute(
            "UPDATE users SET password_hash=? WHERE id=?",
            (hash_password(new_password), user["id"]),
        )
    return templates.TemplateResponse(
        "account.html",
        {"request": request, "user": user, "message": "密码已修改。"},
    )


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request, admin: sqlite3.Row = Depends(require_admin)):
    with db_connect() as conn:
        users = conn.execute(
            "SELECT id, username, role, created_at FROM users ORDER BY id ASC"
        ).fetchall()
        clothes = conn.execute(
            """
            SELECT c.*, u.username AS owner
            FROM clothes c
            LEFT JOIN users u ON u.id = c.user_id
            ORDER BY datetime(c.created_at) DESC, c.id DESC
            """
        ).fetchall()
    return templates.TemplateResponse(
        "admin.html",
        {"request": request, "user": admin, "users": users, "clothes": clothes},
    )
