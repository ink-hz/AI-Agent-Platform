# AI 工程笔记 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有企业登录平台中增加“AI 工程笔记”顶级入口，提供认证后可访问的分类文件树、Markdown 长文阅读器和严格的仓库内容发布门禁。

**Architecture:** 后端从 `backend/app/ai_notes/content/` 构建一次只读白名单索引，以认证、`no-store` 的 API 返回目录元数据和单篇原始 Markdown；任何内容错误只把该模块置为 503，不阻断平台启动。前端只获取 API 数据，通过独立的页面、目录树和文章渲染组件实现深链接、搜索、响应式抽屉、GFM、代码高亮和本地 Mermaid，绝不把文章导入公开 JavaScript bundle。

**Tech Stack:** Python 3.11、FastAPI、Pydantic 2、PyYAML、pytest；React 19、TypeScript、Vite、Vitest、react-markdown、remark-gfm、highlight.js、Mermaid、DOMPurify。

## Global Constraints

- 正式名称与顶级导航文案固定为 `AI 工程笔记`；入口固定为 `/ai-notes`，深链接固定为 `/ai-notes/{category_slug}/{article_slug}`。
- Agent 大脑首页主体保持不变，不增加文章推荐卡片、营销文案、学习路径或课程系统。
- 首期生产内容只有五个空分类：`基础与原理`、`Agent 架构`、`工具与框架`、`AI 工程实践`、`思考与方法`；不迁移任何个人博客文章，也不修改 `/Users/neo/Developer/personal/starship-blog-source`。
- 内容唯一运行时来源是 `backend/app/ai_notes/content/`；不新增数据库、内容写 API、CMS、在线编辑器或文件监听。
- 分类 slug 必须匹配 `[a-z0-9][a-z0-9-]{0,63}`；文章 slug 必须匹配 `[a-z0-9][a-z0-9-]{0,127}`。
- `publishedAt` 不得早于 `2026-05-25`，不得晚于校验当天；`updatedAt` 不得早于 `publishedAt`；未发布文章必须使用 `draft: true`。
- 草稿和正文均必须通过结构校验；草稿不出现在目录中，且深链接返回 404。
- 全部已认证角色可读；未认证请求返回 401；所有目录和正文 API 响应使用 `Cache-Control: no-store`。
- 文章正文不得进入登录页可访问的 JavaScript、CSS 或其他公共静态资产。
- 不启用 Markdown 原始 HTML；拒绝危险 URL；Mermaid 只使用本地依赖和 `securityLevel: "strict"`，单图失败只降级该图。
- 内容错误不得在响应或日志中泄露 Markdown 正文、绝对路径、拒绝清单内容；AI 工程笔记不可用不得影响平台其他页面和健康接口。
- 首期不包含点赞、评论、收藏、进度、统计、推荐、全文搜索、多作者或自动同步。

---

## File map

Backend files:

- `backend/app/ai_notes/models.py`：Pydantic frontmatter 与 API 数据模型。
- `backend/app/ai_notes/repository.py`：安全扫描内容目录、构建有序白名单索引、派生阅读时长。
- `backend/app/ai_notes/validation.py`：日期、链接和旧组织标记发布校验。
- `backend/app/ai_notes/validate.py`：CI/本地可调用的 `python -m app.ai_notes.validate` 入口。
- `backend/app/ai_notes/routes.py`：目录和文章只读 API，统一 404/503/no-store。
- `backend/app/ai_notes/content/*/_index.md`：五个初始空分类。
- `backend/app/ai_notes/legacy_markers.yaml`：版本化旧组织标记清单，首期为空。

Frontend files:

- `webui/src/aiNotesTypes.ts`：API 合同及运行时响应校验。
- `webui/src/aiNotesApi.ts`：认证目录和文章请求客户端。
- `webui/src/pages/AiNotesPage.tsx`：路由、加载、默认选择和错误保留协调。
- `webui/src/components/ai-notes/AiNotesTree.tsx`：分类树、搜索、折叠与移动抽屉。
- `webui/src/components/ai-notes/AiNoteArticle.tsx`：文章标题、路径、元数据和正文布局。
- `webui/src/components/ai-notes/ArticleMarkdown.tsx`：安全 Markdown、锚点、表格、代码和 Mermaid 分派。
- `webui/src/components/ai-notes/MermaidDiagram.tsx`：本地延迟加载 Mermaid、净化 SVG 和单图降级。

### Task 1: 内容模型、只读索引和五个空分类

**Files:**
- Create: `backend/app/ai_notes/__init__.py`
- Create: `backend/app/ai_notes/models.py`
- Create: `backend/app/ai_notes/repository.py`
- Create: `backend/app/ai_notes/content/01-foundations/_index.md`
- Create: `backend/app/ai_notes/content/02-agent-architecture/_index.md`
- Create: `backend/app/ai_notes/content/03-tools-and-frameworks/_index.md`
- Create: `backend/app/ai_notes/content/04-ai-engineering/_index.md`
- Create: `backend/app/ai_notes/content/05-thinking-and-methods/_index.md`
- Test: `backend/tests/test_ai_notes_repository.py`

**Interfaces:**
- Produces: `AiNotesRepository.load(root: Path, *, today: date) -> AiNotesRepository`
- Produces: `AiNotesRepository.index() -> AiNotesIndex`
- Produces: `AiNotesRepository.article(category_slug: str, article_slug: str) -> AiNoteArticle | None`
- Produces: `iter_validated_articles(root: Path, *, today: date) -> Iterator[tuple[Path, ArticleFrontmatter, str]]`
- Produces: `AiNotesContentError`, whose public string is always `AI notes content unavailable`

- [ ] **Step 1: Write failing repository contract tests**

```python
from datetime import date
from pathlib import Path

import pytest

from app.ai_notes.repository import AiNotesContentError, AiNotesRepository


def write_category(root: Path, folder: str, title: str, slug: str) -> Path:
    category = root / folder
    category.mkdir()
    (category / "_index.md").write_text(
        f"---\ntitle: {title}\nslug: {slug}\n---\n", encoding="utf-8"
    )
    return category


def write_article(category: Path, filename: str, *, slug: str, draft: bool = False) -> None:
    category.joinpath(filename).write_text(
        "---\n"
        "title: Agent 系统手册\n"
        f"slug: {slug}\n"
        "description: 从原理到实践。\n"
        "publishedAt: 2026-08-27\n"
        "updatedAt: 2026-08-27\n"
        "tags: [Agent, 架构]\n"
        f"draft: {'true' if draft else 'false'}\n"
        "---\n\n# 正文\n\n内容。\n",
        encoding="utf-8",
    )


def test_builds_ordered_published_index_and_whitelist(tmp_path: Path) -> None:
    second = write_category(tmp_path, "02-tools", "工具与框架", "tools")
    first = write_category(tmp_path, "01-foundations", "基础与原理", "foundations")
    write_article(first, "02-second.md", slug="second")
    write_article(first, "01-first.md", slug="first")
    write_article(second, "01-draft.md", slug="draft", draft=True)

    repository = AiNotesRepository.load(tmp_path, today=date(2026, 8, 27))

    assert [item.slug for item in repository.index().categories] == ["foundations", "tools"]
    assert [item.slug for item in repository.index().categories[0].articles] == ["first", "second"]
    assert repository.article("tools", "draft") is None
    assert repository.article("foundations", "first").markdown.startswith("# 正文")
    assert repository.article("foundations", "first").reading_minutes == 1


@pytest.mark.parametrize("requested", [("../foundations", "first"), ("foundations", "%2e%2e")])
def test_only_prebuilt_keys_can_be_read(tmp_path: Path, requested: tuple[str, str]) -> None:
    category = write_category(tmp_path, "01-foundations", "基础与原理", "foundations")
    write_article(category, "01-first.md", slug="first")
    repository = AiNotesRepository.load(tmp_path, today=date(2026, 8, 27))
    assert repository.article(*requested) is None


def test_duplicate_article_slug_is_a_generic_content_error(tmp_path: Path) -> None:
    category = write_category(tmp_path, "01-foundations", "基础与原理", "foundations")
    write_article(category, "01-first.md", slug="same")
    write_article(category, "02-second.md", slug="same")
    with pytest.raises(AiNotesContentError, match="^AI notes content unavailable$"):
        AiNotesRepository.load(tmp_path, today=date(2026, 8, 27))
```

- [ ] **Step 2: Run the focused test and verify the missing module failure**

Run: `cd backend && pytest -q tests/test_ai_notes_repository.py`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'app.ai_notes'`.

- [ ] **Step 3: Implement the models and repository**

Use strict Pydantic models with `extra="forbid"`, aliases for camelCase frontmatter, and separate snake_case API models:

```python
# backend/app/ai_notes/models.py
from datetime import date
from pydantic import BaseModel, ConfigDict, Field


class CategoryFrontmatter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1)
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")


class ArticleFrontmatter(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    title: str = Field(min_length=1)
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    description: str = Field(min_length=1)
    published_at: date = Field(alias="publishedAt")
    updated_at: date | None = Field(default=None, alias="updatedAt")
    tags: tuple[str, ...] = ()
    draft: bool


class AiNoteSummary(BaseModel):
    slug: str
    title: str
    filename: str
    description: str
    published_at: date
    updated_at: date | None
    tags: tuple[str, ...]
    reading_minutes: int


class AiNoteCategory(BaseModel):
    slug: str
    title: str
    articles: tuple[AiNoteSummary, ...]


class AiNotesIndex(BaseModel):
    categories: tuple[AiNoteCategory, ...]


class AiNoteArticle(AiNoteSummary):
    category_slug: str
    category_title: str
    markdown: str
```

`repository.py` must parse exactly one opening/closing `---` block with `yaml.safe_load`, reject BOMs, symlinks, non-directory root children, files other than `_index.md` and `.md`, missing `_index.md`, duplicate slugs and invalid dates. Build article objects from the scan and retain them only in a dictionary keyed by validated slugs:

```python
class AiNotesContentError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("AI notes content unavailable")


class AiNotesRepository:
    def __init__(self, index: AiNotesIndex, articles: dict[tuple[str, str], AiNoteArticle]) -> None:
        self._index = index
        self._articles = articles

    @classmethod
    def load(cls, root: Path, *, today: date) -> "AiNotesRepository":
        try:
            return _load_repository(root, today=today)
        except AiNotesContentError:
            raise
        except Exception:
            raise AiNotesContentError() from None

    def index(self) -> AiNotesIndex:
        return self._index

    def article(self, category_slug: str, article_slug: str) -> AiNoteArticle | None:
        if CATEGORY_SLUG.fullmatch(category_slug) is None or ARTICLE_SLUG.fullmatch(article_slug) is None:
            return None
        return self._articles.get((category_slug, article_slug))


def reading_minutes(markdown: str) -> int:
    visible_units = len(re.sub(r"\s+", "", markdown))
    return max(1, math.ceil(visible_units / 500))
```

The scanner sorts folders/files by the leading numeric prefix plus full name, stores `filename` without the numeric prefix and `.md`, validates `published_at >= date(2026, 5, 25)`, `published_at <= today`, and `updated_at is None or updated_at >= published_at`. It validates drafts but skips adding them to the index and article dictionary.

- [ ] **Step 4: Add the five empty production categories**

Each file contains only its exact frontmatter. For example:

```markdown
---
title: 基础与原理
slug: foundations
---
```

Use these folder/title/slug triples: `01-foundations` / `基础与原理` / `foundations`; `02-agent-architecture` / `Agent 架构` / `agent-architecture`; `03-tools-and-frameworks` / `工具与框架` / `tools-and-frameworks`; `04-ai-engineering` / `AI 工程实践` / `ai-engineering`; `05-thinking-and-methods` / `思考与方法` / `thinking-and-methods`.

- [ ] **Step 5: Run repository tests and production-content smoke test**

Run: `cd backend && pytest -q tests/test_ai_notes_repository.py && python -c 'from datetime import date; from pathlib import Path; from app.ai_notes.repository import AiNotesRepository; r=AiNotesRepository.load(Path("app/ai_notes/content"), today=date.today()); assert len(r.index().categories)==5; assert sum(len(c.articles) for c in r.index().categories)==0'`

Expected: repository tests PASS; smoke command exits 0.

- [ ] **Step 6: Commit**

```bash
git add backend/app/ai_notes backend/tests/test_ai_notes_repository.py
git commit -m "feat: add AI notes content repository"
```

### Task 2: 发布校验门禁

**Files:**
- Create: `backend/app/ai_notes/validation.py`
- Create: `backend/app/ai_notes/validate.py`
- Create: `backend/app/ai_notes/legacy_markers.yaml`
- Test: `backend/tests/test_ai_notes_validation.py`

**Interfaces:**
- Consumes: `AiNotesRepository.load(root: Path, *, today: date)`
- Produces: `validate_publication(root: Path, marker_file: Path, *, today: date) -> AiNotesIndex`
- Produces: CLI exit 0 with `AI notes content valid: 5 categories, 0 published articles`, or exit 1 with only `AI notes content validation failed`

- [ ] **Step 1: Write failing safety and publication tests**

```python
from datetime import date
from pathlib import Path

import pytest

from app.ai_notes.repository import AiNotesContentError, AiNotesRepository
from app.ai_notes.validation import validate_publication
from test_ai_notes_repository import write_article, write_category


def test_rejects_symlinks_and_unknown_files(tmp_path: Path) -> None:
    category = write_category(tmp_path, "01-foundations", "基础与原理", "foundations")
    (category / "asset.png").write_bytes(b"not allowed")
    with pytest.raises(AiNotesContentError):
        AiNotesRepository.load(tmp_path, today=date(2026, 8, 27))


def test_rejects_article_symlink(tmp_path: Path) -> None:
    category = write_category(tmp_path, "01-foundations", "基础与原理", "foundations")
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    category.joinpath("01-link.md").symlink_to(outside)
    with pytest.raises(AiNotesContentError):
        AiNotesRepository.load(tmp_path, today=date(2026, 8, 27))


@pytest.mark.parametrize(
    ("published", "updated"),
    [("2026-05-24", "2026-05-24"), ("2026-08-28", "2026-08-28"), ("2026-08-27", "2026-08-26")],
)
def test_rejects_invalid_publication_dates(tmp_path: Path, published: str, updated: str) -> None:
    category = write_category(tmp_path, "01-foundations", "基础与原理", "foundations")
    write_article(category, "01-old.md", slug="old")
    path = category / "01-old.md"
    source = path.read_text(encoding="utf-8")
    source = source.replace("publishedAt: 2026-08-27", f"publishedAt: {published}")
    source = source.replace("updatedAt: 2026-08-27", f"updatedAt: {updated}")
    path.write_text(source, encoding="utf-8")
    with pytest.raises(AiNotesContentError):
        AiNotesRepository.load(tmp_path, today=date(2026, 8, 27))


def test_publication_rejects_markers_and_dangerous_links_without_leaking_values(tmp_path: Path) -> None:
    category = write_category(tmp_path, "01-foundations", "基础与原理", "foundations")
    write_article(category, "01-first.md", slug="first")
    article = category / "01-first.md"
    article.write_text(article.read_text(encoding="utf-8") + "\n旧组织代号\n[x](javascript:alert(1))\n", encoding="utf-8")
    markers = tmp_path / "markers.yaml"
    markers.write_text("markers:\n  - 旧组织代号\n", encoding="utf-8")
    with pytest.raises(AiNotesContentError) as raised:
        validate_publication(tmp_path, markers, today=date(2026, 8, 27))
    assert str(raised.value) == "AI notes content unavailable"
    assert "旧组织代号" not in str(raised.value)
```

- [ ] **Step 2: Run the validation test and verify it fails**

Run: `cd backend && pytest -q tests/test_ai_notes_validation.py`

Expected: FAIL with `ModuleNotFoundError: No module named 'app.ai_notes.validation'`.

- [ ] **Step 3: Implement the publication validator and CLI**

`legacy_markers.yaml` starts with exactly:

```yaml
markers: []
```

`validation.py` first calls the repository loader so drafts are structurally checked, then rescans only regular non-draft Markdown using the same frontmatter parser. Parse the marker file as `{markers: list[str]}`, reject empty/non-string entries, compare with `casefold()`, and reject any Markdown link destination whose normalized scheme is not empty, `http`, `https`, or `mailto`. Never include the filename, marker, body, link or absolute root in exceptions.

```python
ALLOWED_LINK_SCHEMES = frozenset({"", "http", "https", "mailto"})
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+['\"][^'\"]*['\"])?\)")


def validate_publication(root: Path, marker_file: Path, *, today: date) -> AiNotesIndex:
    repository = AiNotesRepository.load(root, today=today)
    try:
        marker_data = yaml.safe_load(marker_file.read_text(encoding="utf-8"))
        markers = marker_data["markers"]
        if not isinstance(markers, list) or any(not isinstance(item, str) or not item.strip() for item in markers):
            raise ValueError
        for article_path, frontmatter, markdown in iter_validated_articles(root, today=today):
            del article_path
            if frontmatter.draft:
                continue
            searchable = f"{frontmatter.model_dump_json()}\n{markdown}".casefold()
            if any(marker.casefold() in searchable for marker in markers):
                raise ValueError
            for destination in MARKDOWN_LINK.findall(markdown):
                if urlsplit(destination.strip("<>" )).scheme.casefold() not in ALLOWED_LINK_SCHEMES:
                    raise ValueError
        return repository.index()
    except AiNotesContentError:
        raise
    except Exception:
        raise AiNotesContentError() from None
```

`validate.py` computes paths relative to its own file, calls `validate_publication(..., today=date.today())`, prints only the fixed success/failure text, and returns status 0/1 through `raise SystemExit(main())`.

- [ ] **Step 4: Run validation tests and the production gate**

Run: `cd backend && pytest -q tests/test_ai_notes_repository.py tests/test_ai_notes_validation.py && python -m app.ai_notes.validate`

Expected: tests PASS and output exactly `AI notes content valid: 5 categories, 0 published articles`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/ai_notes backend/tests/test_ai_notes_validation.py
git commit -m "feat: validate AI notes publication content"
```

### Task 3: 认证 API、Shell 路由和故障隔离

**Files:**
- Create: `backend/app/ai_notes/routes.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/control_plane/authorization.py`
- Modify: `backend/app/control_plane/routes_auth.py`
- Test: `backend/tests/test_ai_notes_api.py`
- Test: `backend/tests/test_dingtalk_auth_api.py`
- Test: `backend/tests/test_r1_authorization.py`

**Interfaces:**
- Consumes: `AiNotesRepository.index()` and `AiNotesRepository.article(category_slug, article_slug)`
- Produces: `build_ai_notes_router(reader: AiNotesReader) -> APIRouter`
- Changes: `create_app(..., ai_notes_reader: AiNotesReader | None = None) -> FastAPI`

- [ ] **Step 1: Write failing API behavior tests**

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai_notes.routes import build_ai_notes_router


class FakeReader:
    def index(self):
        return {"categories": []}

    def article(self, category_slug: str, article_slug: str):
        if (category_slug, article_slug) == ("foundations", "handbook"):
            return {
                "slug": "handbook", "title": "手册", "filename": "handbook",
                "description": "说明", "published_at": "2026-08-27", "updated_at": None,
                "tags": [], "reading_minutes": 1, "category_slug": "foundations",
                "category_title": "基础与原理", "markdown": "# INTERNAL_BODY_SENTINEL",
            }
        return None


def test_index_and_article_are_no_store_and_unknown_keys_are_404() -> None:
    app = FastAPI()
    app.include_router(build_ai_notes_router(FakeReader()))
    client = TestClient(app)
    assert client.get("/api/v1/ai-notes").headers["cache-control"] == "no-store"
    article = client.get("/api/v1/ai-notes/foundations/handbook")
    assert article.status_code == 200
    assert article.json()["markdown"] == "# INTERNAL_BODY_SENTINEL"
    assert article.headers["cache-control"] == "no-store"
    assert client.get("/api/v1/ai-notes/foundations/missing").status_code == 404


class BrokenReader:
    def index(self):
        raise RuntimeError("/private/path SECRET_BODY marker-value")

    def article(self, category_slug: str, article_slug: str):
        raise RuntimeError("/private/path SECRET_BODY marker-value")


def test_content_failure_is_generic_503() -> None:
    app = FastAPI()
    app.include_router(build_ai_notes_router(BrokenReader()))
    response = TestClient(app).get("/api/v1/ai-notes")
    assert response.status_code == 503
    assert response.json() == {"detail": "AI notes unavailable"}
    assert "/private/path" not in response.text
    assert "SECRET_BODY" not in response.text
```

Extend existing authorization parameterization with all four exact routes:

```python
("GET", "/api/v1/ai-notes"),
("GET", "/api/v1/ai-notes/{category_slug}/{article_slug}"),
("GET", "/ai-notes"),
("GET", "/ai-notes/{client_path:path}"),
```

- [ ] **Step 2: Run focused backend tests and verify failure**

Run: `cd backend && pytest -q tests/test_ai_notes_api.py tests/test_r1_authorization.py tests/test_dingtalk_auth_api.py -k 'ai_notes or authenticated_root_and_product_routes or exact_authenticated'`

Expected: FAIL because the route builder and authorization entries do not exist.

- [ ] **Step 3: Implement API routes with generic error handling**

```python
# backend/app/ai_notes/routes.py
from typing import Protocol
from fastapi import APIRouter, HTTPException, Response


class AiNotesReader(Protocol):
    def index(self):
        raise NotImplementedError

    def article(self, category_slug: str, article_slug: str):
        raise NotImplementedError


def build_ai_notes_router(reader: AiNotesReader) -> APIRouter:
    router = APIRouter(prefix="/api/v1/ai-notes", tags=["ai-notes"])

    @router.get("")
    def index(response: Response):
        response.headers["Cache-Control"] = "no-store"
        try:
            return reader.index()
        except Exception:
            raise HTTPException(503, "AI notes unavailable") from None

    @router.get("/{category_slug}/{article_slug}")
    def article(category_slug: str, article_slug: str, response: Response):
        response.headers["Cache-Control"] = "no-store"
        try:
            selected = reader.article(category_slug, article_slug)
        except Exception:
            raise HTTPException(503, "AI notes unavailable") from None
        if selected is None:
            raise HTTPException(404, "AI note not found")
        return selected

    return router
```

- [ ] **Step 4: Wire startup without allowing content failure to stop the app**

Add optional `ai_notes_reader` to `create_app`. When identity is enabled and no reader is injected, attempt `AiNotesRepository.load(Path(__file__).parent / "ai_notes/content", today=date.today())`; on failure assign an `UnavailableAiNotesReader` whose two methods raise `AiNotesContentError`. Include `build_ai_notes_router(ai_notes_reader)` only in identity mode. Do not log the caught exception object.

Add both `/ai-notes` decorators to `authenticated_shell`, and add all four routes to `_AUTHENTICATED_SELF_ROUTES`. Extend the Shell test path tuple with `/ai-notes` and `/ai-notes/foundations/handbook`. Add an integration test that injects `BrokenReader`, verifies `/api/v1/ai-notes` is 503, then verifies `/api/health` and `/account` still respond normally.

- [ ] **Step 5: Run full identity and AI notes backend tests**

Run: `cd backend && pytest -q tests/test_ai_notes_repository.py tests/test_ai_notes_validation.py tests/test_ai_notes_api.py tests/test_r1_authorization.py tests/test_dingtalk_auth_api.py`

Expected: all selected tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/ai_notes/routes.py backend/app/main.py backend/app/control_plane/authorization.py backend/app/control_plane/routes_auth.py backend/tests/test_ai_notes_api.py backend/tests/test_dingtalk_auth_api.py backend/tests/test_r1_authorization.py
git commit -m "feat: expose authenticated AI notes API"
```

### Task 4: 前端 API 合同、路由和顶级入口

**Files:**
- Create: `webui/src/aiNotesTypes.ts`
- Create: `webui/src/aiNotesApi.ts`
- Create: `webui/src/aiNotesApi.test.ts`
- Modify: `webui/src/router.ts`
- Modify: `webui/src/router.test.ts`
- Modify: `webui/src/auth.ts`
- Modify: `webui/src/auth.test.ts`
- Modify: `webui/src/AppShell.tsx`
- Modify: `webui/src/AppShell.brain.test.tsx`
- Modify: `webui/src/documentTitle.ts`
- Modify: `webui/src/documentTitle.test.tsx`

**Interfaces:**
- Produces: `AiNotesClient.fetchIndex(signal?: AbortSignal): Promise<AiNotesIndex>`
- Produces: `AiNotesClient.fetchArticle(categorySlug: string, articleSlug: string, signal?: AbortSignal): Promise<AiNoteArticle>`
- Produces routes `{ name: "ai-notes" }` and `{ name: "ai-note"; categorySlug: string; articleSlug: string }`

- [ ] **Step 1: Write failing client and route tests**

```typescript
it("parses and serializes AI notes routes", () => {
  expect(parseRoute("/ai-notes")).toEqual({ name: "ai-notes" });
  const route = parseRoute("/ai-notes/agent-architecture/system-handbook");
  expect(route).toEqual({ name: "ai-note", categorySlug: "agent-architecture", articleSlug: "system-handbook" });
  expect(routePath(route)).toBe("/ai-notes/agent-architecture/system-handbook");
  expect(routeSection(route)).toBe("ai-notes");
});

it("rejects extra AI notes path segments", () => {
  expect(parseRoute("/ai-notes/a/b/c")).toEqual({ name: "not-found" });
});

it("accepts safe AI notes login return paths", () => {
  expect(loginReturnPath("?return_path=/ai-notes")).toBe("/ai-notes");
  expect(loginReturnPath("?return_path=/ai-notes/agent-architecture/system-handbook")).toBe("/ai-notes/agent-architecture/system-handbook");
  expect(loginReturnPath("?return_path=/ai-notes/a/../../admin")).toBe("/");
});
```

For `aiNotesApi.test.ts`, mock `fetch`, return the exact API fixture from the design spec, assert runtime validation accepts it, assert unknown keys or malformed dates reject with `AiNotesContractError`, and assert 404/503 become `AiNotesApiError` with the original status.

- [ ] **Step 2: Run focused frontend tests and verify failure**

Run: `cd webui && npm test -- --run src/aiNotesApi.test.ts src/router.test.ts src/auth.test.ts src/AppShell.brain.test.tsx src/documentTitle.test.tsx`

Expected: FAIL because AI notes types, client and routes do not exist.

- [ ] **Step 3: Implement strict TypeScript contracts and fetch client**

```typescript
export interface AiNoteSummary {
  slug: string; title: string; filename: string; description: string;
  published_at: string; updated_at: string | null; tags: string[]; reading_minutes: number;
}
export interface AiNoteCategory { slug: string; title: string; articles: AiNoteSummary[]; }
export interface AiNotesIndex { categories: AiNoteCategory[]; }
export interface AiNoteArticle extends AiNoteSummary {
  category_slug: string; category_title: string; markdown: string;
}
export class AiNotesContractError extends Error {}
export class AiNotesApiError extends Error {
  constructor(public readonly status: number) { super(`AI notes API ${status}`); }
}
```

Implement exact-key runtime guards, ISO `YYYY-MM-DD` date validation, non-negative arrays and `reading_minutes >= 1`. `aiNotesApi.ts` uses `platformPath`, `credentials: "same-origin"`, `Accept: "application/json"`, and throws `AiNotesApiError(response.status)` before parsing non-2xx bodies. Export a default `aiNotesClient` plus the `AiNotesClient` interface for test injection.

- [ ] **Step 4: Add router, login return path, navigation and document title**

Add the two route union members, `"ai-notes"` to `RouteSection`, strict regex parsing before generic fallthrough, encoded serialization, and `routeSection` mapping. Extend `LoginReturnPath` and `safeLoginReturnPath` with only `/ai-notes` and two slug-regex segments. Add `{ label: "AI 工程笔记", path: "/ai-notes", section: "ai-notes" }` to `USE_NAVIGATION`, extend `NavigationItem.section`, and map both routes to `AI 工程笔记 · Orbbec Agent Platform`.

- [ ] **Step 5: Run focused frontend tests**

Run: `cd webui && npm test -- --run src/aiNotesApi.test.ts src/router.test.ts src/auth.test.ts src/AppShell.brain.test.tsx src/documentTitle.test.tsx`

Expected: all selected tests PASS.

- [ ] **Step 6: Commit**

```bash
git add webui/src/aiNotesTypes.ts webui/src/aiNotesApi.ts webui/src/aiNotesApi.test.ts webui/src/router.ts webui/src/router.test.ts webui/src/auth.ts webui/src/auth.test.ts webui/src/AppShell.tsx webui/src/AppShell.brain.test.tsx webui/src/documentTitle.ts webui/src/documentTitle.test.tsx
git commit -m "feat: add AI notes navigation and client"
```

### Task 5: 页面状态、分类树、搜索与深链接

**Files:**
- Create: `webui/src/pages/AiNotesPage.tsx`
- Create: `webui/src/pages/AiNotesPage.test.tsx`
- Create: `webui/src/components/ai-notes/AiNotesTree.tsx`
- Create: `webui/src/components/ai-notes/AiNotesTree.test.tsx`
- Modify: `webui/src/App.tsx`

**Interfaces:**
- Consumes: `AiNotesClient`, `AiNotesIndex`, `AiNoteArticle`, route slugs and `navigate()`
- Produces: `<AiNotesPage categorySlug?: string articleSlug?: string client?: AiNotesClient />`
- Produces: `<AiNotesTree index selectedPath onSelect />`

- [ ] **Step 1: Write failing page-state tests**

```typescript
it("opens the first published article and replaces the index URL", async () => {
  const client = fakeClient(indexWithTwoArticles, handbookArticle);
  await act(async () => root.render(<AiNotesPage client={client} />));
  expect(client.fetchArticle).toHaveBeenCalledWith("foundations", "handbook", expect.any(AbortSignal));
  expect(mockNavigate).toHaveBeenCalledWith("/ai-notes/foundations/handbook", { replace: true });
});

it("treats five empty categories as a valid empty state", async () => {
  await act(async () => root.render(<AiNotesPage client={fakeClient(emptyFiveCategoryIndex)} />));
  expect(container.textContent).toContain("暂无已发布文章");
  expect(container.textContent).not.toContain("暂时不可用");
});

it("keeps the previous article when the next request fails", async () => {
  const client = switchingClient(handbookArticle, new AiNotesApiError(503));
  await act(async () => root.render(<AiNotesPage categorySlug="foundations" articleSlug="handbook" client={client} />));
  await act(async () => root.render(<AiNotesPage categorySlug="tools" articleSlug="frameworks" client={client} />));
  expect(container.textContent).toContain("Agent 系统手册");
  expect(container.textContent).toContain("文章暂时无法打开");
});

it("keeps the tree on a deep-link 404", async () => {
  const client = fakeClient(indexWithTwoArticles, new AiNotesApiError(404));
  await act(async () => root.render(<AiNotesPage categorySlug="foundations" articleSlug="missing" client={client} />));
  expect(container.textContent).toContain("基础与原理");
  expect(container.textContent).toContain("文章不存在");
});
```

Tree tests must assert category counts, initial expansion of the selected category, collapse behavior, current `aria-current="page"`, search matching title/filename/category, and `没有匹配的文章` without invoking `onSelect`.

- [ ] **Step 2: Run focused page tests and verify failure**

Run: `cd webui && npm test -- --run src/pages/AiNotesPage.test.tsx src/components/ai-notes/AiNotesTree.test.tsx`

Expected: FAIL because both components are missing.

- [ ] **Step 3: Implement the tree as a controlled selection component**

`AiNotesTree` stores only `query` and `expanded: Set<string>` locally. Its article buttons call `onSelect(category.slug, article.slug)`; filtering uses `toLocaleLowerCase("zh-CN")` over category title, article title and `filename`. When a selected path changes, expand that category. Render semantic buttons with `aria-expanded`, a search input labelled `搜索文章`, counts from the published arrays, and a live no-results status. Searching never changes selection.

- [ ] **Step 4: Implement page orchestration and App routing**

`AiNotesPage` fetches the index once per client, aborts on unmount, and distinguishes `loading`, `index-error`, `empty`, `article-error`, `not-found`, and `ready`. With no route slugs, choose the first article in category/file order and call `navigate(path, { replace: true })`. On route changes, keep `lastSuccessfulArticle` visible until a replacement succeeds. Ignore stale/aborted responses using an incrementing request token. In `App.tsx`, allow `ai-notes`/`ai-note` for `management_viewer`, and render:

```tsx
case "ai-notes":
  return account ? <AiNotesPage /> : <PendingPage title="AI 工程笔记" description="请启用企业身份后阅读。" />;
case "ai-note":
  return account ? <AiNotesPage categorySlug={route.categorySlug} articleSlug={route.articleSlug} />
    : <PendingPage title="AI 工程笔记" description="请启用企业身份后阅读。" />;
```

- [ ] **Step 5: Run page, tree and App tests**

Run: `cd webui && npm test -- --run src/pages/AiNotesPage.test.tsx src/components/ai-notes/AiNotesTree.test.tsx src/App.test.tsx`

Expected: all selected tests PASS.

- [ ] **Step 6: Commit**

```bash
git add webui/src/pages/AiNotesPage.tsx webui/src/pages/AiNotesPage.test.tsx webui/src/components/ai-notes/AiNotesTree.tsx webui/src/components/ai-notes/AiNotesTree.test.tsx webui/src/App.tsx
git commit -m "feat: add AI notes tree and page state"
```

### Task 6: 安全长文 Markdown、代码高亮和 Mermaid

**Files:**
- Create: `webui/src/components/ai-notes/ArticleMarkdown.tsx`
- Create: `webui/src/components/ai-notes/ArticleMarkdown.test.tsx`
- Create: `webui/src/components/ai-notes/MermaidDiagram.tsx`
- Create: `webui/src/components/ai-notes/MermaidDiagram.test.tsx`
- Create: `webui/src/components/ai-notes/AiNoteArticle.tsx`
- Modify: `webui/package.json`
- Modify: `webui/package-lock.json`

**Interfaces:**
- Consumes: `AiNoteArticle`
- Produces: `<AiNoteArticle article: AiNoteArticle />`
- Produces: `<ArticleMarkdown markdown: string />`
- Produces: `<MermaidDiagram source: string />`

- [ ] **Step 1: Write failing renderer tests**

```typescript
it("renders GFM, stable heading anchors, highlighted code and safe external links", () => {
  const html = renderToStaticMarkup(<ArticleMarkdown markdown={
    "## 标题\n\n## 标题\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n```ts\nconst n = 1\n```\n\n[外部](https://example.com)"
  } />);
  expect(html).toContain('id="标题"');
  expect(html).toContain('id="标题-2"');
  expect(html).toContain('class="article-table-scroll"');
  expect(html).toContain('class="hljs language-ts"');
  expect(html).toContain('target="_blank"');
  expect(html).toContain('rel="noopener noreferrer"');
});

it("does not render raw HTML or dangerous protocols", () => {
  const html = renderToStaticMarkup(<ArticleMarkdown markdown={
    '<script>alert(1)</script>\n\n[x](javascript:alert(1))\n\n[y](data:text/html,bad)'
  } />);
  expect(html).not.toContain("<script>");
  expect(html).not.toContain('href="javascript:');
  expect(html).not.toContain('href="data:');
});
```

Mermaid tests mock `mermaid` and `dompurify`: one test resolves `<svg><text>ok</text></svg>` and asserts a labelled diagram appears; another rejects and asserts `图表暂时无法渲染` plus the original source while the surrounding Markdown remains rendered.

- [ ] **Step 2: Run renderer tests and verify failure**

Run: `cd webui && npm test -- --run src/components/ai-notes/ArticleMarkdown.test.tsx src/components/ai-notes/MermaidDiagram.test.tsx`

Expected: FAIL because renderer components are missing.

- [ ] **Step 3: Install local rendering dependencies**

Run: `cd webui && npm install dompurify@^3.2.6 highlight.js@^11.11.1 mermaid@^11.9.0`

Expected: `package.json` and `package-lock.json` contain the three runtime dependencies; audit output has no install failure.

- [ ] **Step 4: Implement ArticleMarkdown**

Use `ReactMarkdown` with `remarkGfm`, no rehype raw plugin, a render-local duplicate-aware heading slug map, and a `urlTransform` that accepts fragment, relative, root-relative, `http:`, `https:` and `mailto:` only. External HTTP(S) anchors receive safe target/rel. Table nodes are wrapped in `article-table-scroll`. For fenced code, extract `language-*`; `mermaid` delegates to `MermaidDiagram`; known highlight.js languages use `hljs.highlight`, then sanitize the generated span-only HTML with DOMPurify; unknown languages render escaped plain code.

- [ ] **Step 5: Implement Mermaid and article chrome**

`MermaidDiagram` calls `import("mermaid")` inside `useEffect`, initializes once with `{ startOnLoad: false, securityLevel: "strict", theme: "neutral" }`, creates an identifier from a module counter containing only letters/digits/hyphens, calls `mermaid.render`, sanitizes SVG with DOMPurify using SVG profiles, and inserts only the sanitized result. On rejection it renders a status plus `<pre><code>{source}</code></pre>`. Cancellation prevents state updates after unmount.

`AiNoteArticle` renders `category_title / filename.md`, title, semantic `<time>` values, tags, `约 N 分钟`, and `<ArticleMarkdown markdown={article.markdown} />`. It must not display `description` as marketing copy above the body.

- [ ] **Step 6: Run renderer tests and production build**

Run: `cd webui && npm test -- --run src/components/ai-notes/ArticleMarkdown.test.tsx src/components/ai-notes/MermaidDiagram.test.tsx && npm run build`

Expected: tests PASS and Vite build exits 0 without remote asset requests.

- [ ] **Step 7: Commit**

```bash
git add webui/package.json webui/package-lock.json webui/src/components/ai-notes/ArticleMarkdown.tsx webui/src/components/ai-notes/ArticleMarkdown.test.tsx webui/src/components/ai-notes/MermaidDiagram.tsx webui/src/components/ai-notes/MermaidDiagram.test.tsx webui/src/components/ai-notes/AiNoteArticle.tsx
git commit -m "feat: render AI notes Markdown securely"
```

### Task 7: 双栏布局、移动抽屉和可访问性

**Files:**
- Modify: `webui/src/pages/AiNotesPage.tsx`
- Modify: `webui/src/pages/AiNotesPage.test.tsx`
- Modify: `webui/src/components/ai-notes/AiNotesTree.tsx`
- Modify: `webui/src/AppShell.tsx`
- Modify: `webui/src/styles.css`
- Modify: `webui/src/styles.test.ts`

**Interfaces:**
- Consumes: completed page/tree/article components
- Produces: full-height desktop split layout and keyboard-safe narrow-screen tree drawer

- [ ] **Step 1: Write failing drawer and layout tests**

```typescript
it("opens and closes the mobile directory and restores focus", async () => {
  await act(async () => root.render(<AiNotesPage client={fakeClient(indexWithTwoArticles, handbookArticle)} />));
  const opener = container.querySelector<HTMLButtonElement>('button[aria-label="打开文章目录"]')!;
  expect(opener.getAttribute("aria-expanded")).toBe("false");
  await act(async () => opener.click());
  expect(container.querySelector('[role="dialog"][aria-label="文章目录"]')).not.toBeNull();
  expect(document.activeElement).toBe(container.querySelector('button[aria-label="关闭文章目录"]'));
  await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })));
  expect(container.querySelector('[role="dialog"][aria-label="文章目录"]')).toBeNull();
  expect(document.activeElement).toBe(opener);
});
```

Add style assertions for `.ai-notes-layout { min-height: 0; display: grid; grid-template-columns: 19rem minmax(0, 1fr); }`, independent `overflow: auto` on sidebar and reader, max article measure, responsive drawer breakpoint, table overflow and visible `:focus-visible` outlines.

- [ ] **Step 2: Run page and style tests and verify failure**

Run: `cd webui && npm test -- --run src/pages/AiNotesPage.test.tsx src/styles.test.ts`

Expected: FAIL because drawer behavior and AI notes CSS are absent.

- [ ] **Step 3: Implement shell sizing, responsive drawer and styles**

Treat both AI notes routes as a dedicated workspace in `AppShell`: add `is-ai-notes-workspace-shell` to the root, `is-ai-notes-workspace` to `<main>`, and suppress the footer just as for the brain workspace. Keep the existing top bar and all visual variables.

On narrow screens, render the same tree inside a labelled dialog with a backdrop. On open, focus the close button; on Escape, close; after close, focus the opener. Close after article selection. The desktop tree remains in the DOM only outside the responsive drawer presentation so duplicate controls are not exposed to screen readers.

CSS must provide a restrained file-tree appearance: no hero, gradients, cards or promotional copy; fixed left rail, subtle borders, monospace-like `.md` file affordance, readable article width, sticky mobile directory button, horizontal table scroll and print-friendly single-column article output.

- [ ] **Step 4: Run focused tests and build**

Run: `cd webui && npm test -- --run src/pages/AiNotesPage.test.tsx src/components/ai-notes/AiNotesTree.test.tsx src/styles.test.ts && npm run build`

Expected: tests PASS and build exits 0.

- [ ] **Step 5: Commit**

```bash
git add webui/src/pages/AiNotesPage.tsx webui/src/pages/AiNotesPage.test.tsx webui/src/components/ai-notes/AiNotesTree.tsx webui/src/AppShell.tsx webui/src/styles.css webui/src/styles.test.ts
git commit -m "feat: finish AI notes reading workspace"
```

### Task 8: 内容保密、部署回归和作者文档

**Files:**
- Create: `backend/app/ai_notes/README.md`
- Modify: `backend/tests/test_ai_notes_api.py`
- Modify: `backend/tests/test_dingtalk_auth_api.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: complete backend/frontend feature
- Produces: regression proof that content is authenticated and absent from public bundles; exact author workflow for future migration

- [ ] **Step 1: Write the failing content-leak regression test**

Build the frontend in the existing static fixture, inject a reader whose article body contains `INTERNAL_AI_NOTE_SENTINEL_8F2C`, and assert:

```python
def test_article_body_requires_auth_and_is_absent_from_public_assets(app_with_ai_notes, static_dir):
    anonymous = TestClient(app_with_ai_notes)
    assert anonymous.get("/api/v1/ai-notes/foundations/handbook").status_code == 401
    public_bytes = b"".join(
        path.read_bytes() for path in static_dir.rglob("*") if path.is_file()
    )
    assert b"INTERNAL_AI_NOTE_SENTINEL_8F2C" not in public_bytes
    authenticated = anonymous.get(
        "/api/v1/ai-notes/foundations/handbook",
        cookies={"__Host-platform_session": "valid-cookie"},
    )
    assert authenticated.status_code == 200
    assert authenticated.json()["markdown"] == "INTERNAL_AI_NOTE_SENTINEL_8F2C"
```

Also assert anonymous `/ai-notes` redirects to login or returns 401 according to the existing middleware contract, while authenticated `/ai-notes` serves only the Shell HTML.

- [ ] **Step 2: Run the security regression and verify it fails before the fixture wiring**

Run: `cd backend && pytest -q tests/test_ai_notes_api.py tests/test_dingtalk_auth_api.py -k 'article_body_requires_auth or ai_notes'`

Expected: FAIL because `app_with_ai_notes` and its injected reader fixture are not yet defined.

- [ ] **Step 3: Complete the fixture and authoring documentation**

Implement the fixture with `create_app(..., identity_auth=FakeAuth(), ai_notes_reader=SentinelReader(), start_poller=False)` and the existing built-static helper. Document in `backend/app/ai_notes/README.md`:

1. exact category/article frontmatter fields and slug/date rules;
2. numeric prefix ordering and stable URL behavior;
3. `draft: true` workflow;
4. command `cd backend && python -m app.ai_notes.validate`;
5. first-migration requirement to populate `legacy_markers.yaml`;
6. one-article-at-a-time copy, cleanup, primary-source technical verification, local rendering review and content-owner approval;
7. prohibition on modifying or runtime-linking the personal blog repository.

Add a short `AI 工程笔记` section to root `README.md` with the content path, validator command and authenticated read-only routes. Do not describe any unpublished article as present.

- [ ] **Step 4: Run all backend and frontend verification**

Run: `cd backend && python -m app.ai_notes.validate && pytest -q`

Expected: validator reports five categories and zero published articles; complete backend suite PASS.

Run: `cd webui && npm test -- --run && npm run build`

Expected: complete frontend suite PASS; Vite production build exits 0.

- [ ] **Step 5: Verify scope and worktree cleanliness**

Run: `git status --short && find backend/app/ai_notes/content -type f -maxdepth 2 -print | sort && rg -n "starship-blog-source|INTERNAL_AI_NOTE_SENTINEL_8F2C" backend/app/ai_notes/content webui/dist || true`

Expected: exactly five `_index.md` files under production content, no migrated article Markdown, no personal-repository reference or sentinel in content/build output; pre-existing unrelated untracked files remain untouched.

- [ ] **Step 6: Commit**

```bash
git add backend/app/ai_notes/README.md backend/tests/test_ai_notes_api.py backend/tests/test_dingtalk_auth_api.py README.md
git commit -m "docs: document AI notes publishing workflow"
```

### Task 9: Final acceptance review

**Files:**
- Review only: `docs/superpowers/specs/2026-08-27-ai-engineering-notes-design.md`
- Review only: all files committed in Tasks 1–8

**Interfaces:**
- Consumes: complete implementation and test evidence
- Produces: a verified handoff with no article migration

- [ ] **Step 1: Run targeted acceptance checks**

Run: `cd backend && pytest -q tests/test_ai_notes_repository.py tests/test_ai_notes_validation.py tests/test_ai_notes_api.py tests/test_r1_authorization.py tests/test_dingtalk_auth_api.py`

Expected: all selected backend tests PASS.

Run: `cd webui && npm test -- --run src/aiNotesApi.test.ts src/router.test.ts src/auth.test.ts src/pages/AiNotesPage.test.tsx src/components/ai-notes/AiNotesTree.test.tsx src/components/ai-notes/ArticleMarkdown.test.tsx src/components/ai-notes/MermaidDiagram.test.tsx src/AppShell.brain.test.tsx src/documentTitle.test.tsx src/styles.test.ts && npm run build`

Expected: all selected frontend tests PASS and build exits 0.

- [ ] **Step 2: Perform a manual browser acceptance pass**

With the existing local authenticated development setup, verify `/ai-notes` shows the five classified empty folders and `暂无已发布文章`; at a narrow viewport verify the directory button, Escape close and focus return; verify Agent 大脑 `/` is visually unchanged; verify direct anonymous requests cannot retrieve `/api/v1/ai-notes` or any article body. Using only a temporary test-content fixture, open one Mermaid article under the production Shell CSP and confirm the local diagram renders without a CSP violation; delete the fixture after the check and do not add it to production content.

- [ ] **Step 3: Confirm no out-of-scope content or storage changes**

Run: `git diff --stat fc2c0aa..HEAD && git diff --name-only fc2c0aa..HEAD | rg 'migrations|starship-blog-source' || true`

Expected: feature code, tests and documentation only; no database migrations and no personal-blog changes.

- [ ] **Step 4: Record final status without creating an extra commit**

Report the validator result, backend/frontend test counts, build result, manual browser result, exact production content count (`5 categories, 0 published articles`), and any pre-existing unrelated dirty files. Do not claim article migration is complete.
