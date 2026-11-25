WITH revision AS (
    SELECT array_agg(version_num) AS heads FROM studio.alembic_version
), release_column AS (
    SELECT atttypid = 'smallint'::regtype AND attnotnull
        AND pg_get_expr(adbin, adrelid) IN ('0', '''0''::smallint') AS verified
    FROM pg_attribute
    LEFT JOIN pg_attrdef ON adrelid = attrelid AND adnum = attnum
    WHERE attrelid = 'studio.media_jobs'::regclass
      AND attname = 'matte_releases' AND NOT attisdropped
), matte_fifo AS (
    SELECT next_position FROM studio.media_job_lanes WHERE lane = 'matte'
), predecessor_keys AS (
    SELECT count(*) AS verified
    FROM pg_constraint
    WHERE contype = 'f' AND convalidated AND (
        (conrelid = 'studio.publications'::regclass AND conname = 'publications_package_run_target_fkey'
            AND confrelid = 'studio.release_packages'::regclass)
        OR (conrelid = 'studio.archive_records'::regclass AND conname = 'archive_records_root_version_fkey'
            AND confrelid = 'studio.archive_versions'::regclass AND condeferrable AND condeferred)
    )
), matte_checks AS (
    SELECT count(*) AS checked,
           count(*) FILTER (WHERE position('''matte''' IN pg_get_expr(conbin, conrelid)) > 0) AS widened
    FROM pg_constraint
    WHERE contype = 'c' AND convalidated AND (conrelid, conname) IN (
        ('studio.worker_lane_grants'::regclass, 'worker_lane_grants_lane_check'),
        ('studio.media_job_lanes'::regclass, 'media_job_lanes_lane_check'),
        ('studio.queue_lane_events'::regclass, 'queue_lane_events_lane_check'),
        ('studio.media_jobs'::regclass, 'media_jobs_kind_check'),
        ('studio.media_jobs'::regclass, 'media_jobs_check1'),
        ('studio.media_jobs'::regclass, 'media_jobs_check2')
    )
)
SELECT CASE
    WHEN current_setting('transaction_read_only') <> 'on' OR pg_is_in_recovery() OR verified <> 2 OR checked <> 6
        THEN 'unverified'
    WHEN heads = ARRAY['0012_release_constraints']::varchar[] AND widened = 0
         AND NOT EXISTS (SELECT FROM release_column) AND NOT EXISTS (SELECT FROM matte_fifo)
        THEN '0012_release_constraints'
    WHEN heads = ARRAY['0013_matte_jobs']::varchar[] AND widened = 6
         AND EXISTS (SELECT FROM release_column WHERE verified)
         AND EXISTS (SELECT FROM matte_fifo WHERE next_position >= 1)
        THEN '0013_matte_jobs'
    ELSE 'unverified'
END
FROM revision CROSS JOIN predecessor_keys CROSS JOIN matte_checks;
