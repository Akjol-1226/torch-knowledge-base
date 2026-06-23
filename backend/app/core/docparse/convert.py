import dataclasses
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .config import get_config
from .models import DocumentContext, Heading, PageContext, PageStructure
from .render import render_pdf_to_images
from .outline import run_phase1, extract_phase1_tail
from .convert_page import build_page_context, convert_page_to_markdown
from .postprocess import postprocess_markdown
from .validators import validate_markdown, repair_markdown
from .utils import get_file_title, extract_tail_text, update_heading_stack
from .structure_enrich import normalize_global_headings
from .relevel import relevel_headings_with_llm

logger = logging.getLogger(__name__)


def precompute_page_contexts(
    document_context: DocumentContext,
    page_raw_texts: dict[int, str],
) -> dict[int, PageContext]:
    """Phase 1 结束后，用 Phase 1 数据为每页预算 PageContext。"""
    stack: list[Heading] = []
    contexts: dict[int, PageContext] = {}
    p1_tail: str | None = None

    # 暂存原始值，循环结束后还原，避免污染 document_context 状态
    original_stack = list(document_context.current_heading_stack)
    original_appendix = document_context.current_appendix
    # 从 Phase 1 结束后的实际状态出发（通常为 None，但不硬编码）
    current_appendix = document_context.current_appendix

    for page_no in range(1, document_context.total_pages + 1):
        ps = document_context.page_structures.get(page_no, PageStructure(page_no=page_no))
        raw_text = page_raw_texts.get(page_no, '')

        # 同时写入预计算的 stack 和 appendix：
        # build_page_context 内部的 deepest_section_level() 会读 document_context.current_heading_stack
        # 来计算 section_level，进而驱动 relevel_table_headings()；
        # 若不在此更新，relevel_table_headings 将对所有页使用 Phase 1 末页栈（错误）
        document_context.current_heading_stack = stack
        document_context.current_appendix = current_appendix

        ctx = build_page_context(
            document_context, page_no,
            previous_page_tail=p1_tail,
            page_raw_text=raw_text,
        )
        # build_page_context 已从 document_context.current_heading_stack（即 stack）
        # 读取了正确值并写入 ctx.current_heading_stack，无需再次覆盖
        contexts[page_no] = ctx

        # 更新栈与附件状态：只用 Phase 1 标题，与顺序循环逻辑保持一致
        all_headings = ps.headings + ps.appendix_headings
        stack = update_heading_stack(stack, all_headings)
        has_new_body = any(h.type == 'body_heading' for h in all_headings)
        if has_new_body:
            current_appendix = None
        elif ps.appendix_headings:
            current_appendix = ps.appendix_headings[-1]

        # Phase 1 tail：用结构摘要（末尾标题、是否跨页表格）代替真实 markdown 末尾
        p1_tail = extract_phase1_tail(ps)

    # 还原，不影响后续逻辑
    document_context.current_heading_stack = original_stack
    document_context.current_appendix = original_appendix
    return contexts


def _run_phase2_parallel(
    page_images: list[Path],
    contexts: dict[int, PageContext],
    max_workers: int = 16,
) -> dict[int, str]:
    """并行跑所有页的 VLM 转换，返回 {page_no: markdown}。"""

    def convert_one(page_no: int) -> tuple[int, str]:
        image_path = page_images[page_no - 1]
        md = convert_page_to_markdown(image_path, contexts[page_no])
        logger.info('Phase 2 page %d done', page_no)
        return page_no, md

    # 预填空字符串，确保任意页失败时 results 仍有完整 key 集合
    results: dict[int, str] = {p: '' for p in range(1, len(page_images) + 1)}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(convert_one, page_no): page_no
            for page_no in range(1, len(page_images) + 1)
        }
        for future in as_completed(futures):
            pno = futures[future]
            try:
                page_no, md = future.result()
                results[page_no] = md
            except Exception as exc:
                logger.error('Phase 2 page %d failed: %s', pno, exc)
                # results[pno] 保持空字符串，不中断其余页面

    return results


def _detect_tail_issue(
    prev_md: str,
    curr_md: str,
) -> bool:
    """判断 page N 是否因 previous_page_tail 不准确而产生续接问题。"""
    prev_lines = [l for l in prev_md.strip().splitlines() if l.strip()]
    curr_lines = [l for l in curr_md.strip().splitlines() if l.strip()]
    if not prev_lines or not curr_lines:
        return False

    prev_ends_table = prev_lines[-1].startswith('|')
    curr_first = curr_lines[0]
    curr_starts_mid_table = (
        curr_first.startswith('|')
        and len(curr_lines) > 1
        and not re.match(r'^\|[-: |]+\|', curr_lines[1])
    )
    curr_starts_mid_sentence = bool(re.match(r'^[a-z，。、；：]', curr_first))

    return (prev_ends_table and curr_starts_mid_table) or curr_starts_mid_sentence


def repair_tail_continuations(
    results: dict[int, str],
    contexts: dict[int, PageContext],
    page_images: list[Path],
    max_tail_chars: int = 300,
) -> dict[int, str]:
    """并行 Phase 2 完成后，对有续接问题的页面用真实 tail 重跑。"""
    pages_to_retry: list[int] = []

    for page_no in range(2, len(page_images) + 1):
        prev_md = results[page_no - 1]
        curr_md = results[page_no]

        if _detect_tail_issue(prev_md, curr_md):
            pages_to_retry.append(page_no)
            logger.info('Page %d: tail issue detected, will retry', page_no)

    if not pages_to_retry:
        return results

    logger.info('Retrying %d pages with correct previous_page_tail', len(pages_to_retry))

    def retry_one(page_no: int) -> tuple[int, str]:
        real_tail = extract_tail_text(results[page_no - 1], max_chars=max_tail_chars)
        # 用 dataclasses.replace 创建副本，避免修改 contexts 中的原始对象
        ctx = dataclasses.replace(contexts[page_no], previous_page_tail=real_tail)
        md = convert_page_to_markdown(page_images[page_no - 1], ctx)
        logger.info('Retry page %d done', page_no)
        return page_no, md

    config = get_config()
    with ThreadPoolExecutor(max_workers=config.phase2_max_workers) as pool:
        retry_futures = {
            pool.submit(retry_one, page_no): page_no
            for page_no in pages_to_retry
        }
        for future in as_completed(retry_futures):
            pno = retry_futures[future]
            try:
                page_no, md = future.result()
                results[page_no] = md
            except Exception as exc:
                logger.error('Retry page %d failed: %s', pno, exc)
                # results[pno] 保持原有结果，不中断其余页面

    return results


def convert_pdf_to_markdown(
    pdf_path: str,
    output_path: str,
    debug: bool = False,
) -> None:
    config = get_config()
    if not config.api_key.strip():
        raise ValueError(
            'QWEN_API_KEY is not set. Add it to .env or environment variables.'
        )
    file_title = get_file_title(pdf_path)
    project_root = Path(pdf_path).parent
    debug_root = project_root / '_debug' / Path(pdf_path).stem

    page_images = render_pdf_to_images(
        pdf_path=pdf_path,
        dpi=config.pdf_render_dpi,
        output_dir=debug_root / 'pages_img',
    )

    document_context = DocumentContext(
        pdf_path=pdf_path,
        file_title=file_title,
        total_pages=len(page_images),
    )

    logger.info('Phase 1: extracting structure from %d pages', len(page_images))
    document_context, page_raw_texts = run_phase1(pdf_path, page_images, document_context)

    logger.info('Phase 1: normalizing heading levels globally...')
    normalize_global_headings(document_context)

    logger.info('Phase 1.5: releveling heading hierarchy with LLM...')
    relevel_headings_with_llm(document_context)

    if debug:
        outline_path = debug_root / 'outline.json'
        outline_path.parent.mkdir(parents=True, exist_ok=True)
        outline_data = {
            str(pno): {
                'is_toc_page': ps.is_toc_page,
                'is_appendix_page': ps.is_appendix_page,
                'is_table_continuation': ps.is_table_continuation,
                'extraction_method': ps.extraction_method,
                'structure_confidence': ps.structure_confidence,
                'headings': [{'text': h.text, 'level': h.level, 'type': h.type} for h in ps.headings],
                'appendix_headings': [{'text': h.text, 'level': h.level} for h in ps.appendix_headings],
                'notes': ps.notes,
            }
            for pno, ps in document_context.page_structures.items()
        }
        outline_path.write_text(json.dumps(outline_data, ensure_ascii=False, indent=2), encoding='utf-8')

    pages_debug_dir = debug_root / 'pages'
    ctx_debug_dir = debug_root / 'page_contexts'
    if debug:
        pages_debug_dir.mkdir(parents=True, exist_ok=True)
        ctx_debug_dir.mkdir(parents=True, exist_ok=True)

    logger.info('Precomputing page contexts...')
    contexts = precompute_page_contexts(document_context, page_raw_texts)

    if debug:
        for page_no, ctx in contexts.items():
            ctx_path = ctx_debug_dir / f'page_{page_no:03d}_context.json'
            ctx_path.write_text(
                json.dumps(dataclasses.asdict(ctx), ensure_ascii=False, indent=2, default=str),
                encoding='utf-8',
            )

    logger.info('Phase 2: converting %d pages in parallel', len(page_images))
    results = _run_phase2_parallel(page_images, contexts, max_workers=config.phase2_max_workers)

    if config.repair_tail_continuations:
        results = repair_tail_continuations(
            results, contexts, page_images,
            max_tail_chars=config.max_previous_tail_chars,
        )

    page_markdowns = [
        f'<!-- page: {page_no} -->\n{results[page_no]}'
        for page_no in range(1, len(page_images) + 1)
    ]

    if debug:
        for page_no, md in results.items():
            (pages_debug_dir / f'page_{page_no:03d}.md').write_text(md, encoding='utf-8')

    raw = '\n\n'.join(page_markdowns)

    if debug:
        pp_dir = debug_root / 'postprocess'
        pp_dir.mkdir(parents=True, exist_ok=True)
        (pp_dir / 'before_postprocess.md').write_text(raw, encoding='utf-8')

    final = postprocess_markdown(raw, file_title, document_context, debug=debug)

    if debug:
        (debug_root / 'postprocess' / 'after_postprocess.md').write_text(final, encoding='utf-8')

    report = validate_markdown(final)
    if debug:
        (debug_root / 'validation_report.json').write_text(
            json.dumps({'errors': report.errors}, ensure_ascii=False, indent=2), encoding='utf-8'
        )
    if report.has_errors:
        logger.warning('Validation errors: %s', report.errors)
        final = repair_markdown(final, report)

    Path(output_path).write_text(final, encoding='utf-8')
    logger.info('Done → %s', output_path)
