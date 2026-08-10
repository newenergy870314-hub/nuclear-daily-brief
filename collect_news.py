    # 실제 화면에 표시할 기사만 대상으로 원문 대표 이미지/설명을 병렬 보완
    enrich_article_metadata(articles_by_period)

    # 대표 이미지를 로컬 파일로 저장해 외부 이미지 차단/로딩 실패를 줄입니다.
    cache_article_thumbnails(articles_by_period)
    cleanup_old_thumbnails(now)

    current_urls = {
        article.link
        for items in articles_by_period.values()
        for article in items
    }
    new_urls = current_urls - previous_urls if previous_urls else set()

    archive = update_archive(
        load_archive(),
        periods,
        articles_by_period,
        now,
    )

    # 10분 예약 실행에서는 Backfill을 끄고,
    # 필요할 때 수동 실행(workflow_dispatch)에서만 채웁니다.
    if SKIP_BACKFILL:
        print("SKIP_BACKFILL=1: historical archive backfill skipped")
    else:
        archive = backfill_missing_archive_dates(archive, now)

    save_archive(archive)

    OUTPUT.write_text(
        build_html(
            periods,
            articles_by_period,
            display_updated_at,
            new_urls,
            archive,
        ),
        encoding="utf-8",
    )

    save_current_urls(current_urls, now)

    total = sum(len(items) for items in articles_by_period.values())
    print(
        f"Generated {OUTPUT}: {total} news articles across 3 periods; "
        f"{len(archive)} archive dates"
    )
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
