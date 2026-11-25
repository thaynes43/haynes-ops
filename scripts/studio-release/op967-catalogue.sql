-- PostgreSQL 16 deparses foreign-key targets with schema names under search_path=pg_catalog.
WITH revision AS (
    SELECT array_agg(version_num) AS heads FROM studio.alembic_version
), expected(relation, name, v12, v13) AS (VALUES
    ('studio.publications'::regclass, 'publications_package_run_target_fkey',
        'FOREIGN KEY (package_sha256, run_id, target_id) REFERENCES studio.release_packages(sha256, run_id, target_id)', NULL),
    ('studio.archive_records'::regclass, 'archive_records_root_version_fkey',
        'FOREIGN KEY (id, root_number) REFERENCES studio.archive_versions(record_id, number) DEFERRABLE INITIALLY DEFERRED', NULL),
    ('studio.worker_lane_grants'::regclass, 'worker_lane_grants_lane_check',
        $$CHECK ((lane = ANY (ARRAY['run'::text, 'voice'::text, 'image'::text])))$$,
        $$CHECK ((lane = ANY (ARRAY['run'::text, 'voice'::text, 'image'::text, 'matte'::text])))$$),
    ('studio.media_job_lanes'::regclass, 'media_job_lanes_lane_check',
        $$CHECK ((lane = ANY (ARRAY['image'::text, 'voice'::text])))$$,
        $$CHECK ((lane = ANY (ARRAY['image'::text, 'voice'::text, 'matte'::text])))$$),
    ('studio.queue_lane_events'::regclass, 'queue_lane_events_lane_check',
        $$CHECK ((lane = ANY (ARRAY['run'::text, 'voice'::text, 'image'::text])))$$,
        $$CHECK ((lane = ANY (ARRAY['run'::text, 'voice'::text, 'image'::text, 'matte'::text])))$$),
    ('studio.media_jobs'::regclass, 'media_jobs_kind_check',
        $$CHECK ((kind = ANY (ARRAY['voice'::text, 'photo'::text, 'asset_candidate'::text])))$$,
        $$CHECK ((kind = ANY (ARRAY['voice'::text, 'photo'::text, 'asset_candidate'::text, 'matte'::text])))$$),
    ('studio.media_jobs'::regclass, 'media_jobs_check1',
        $$CHECK (((kind = 'voice'::text) = (lane = 'voice'::text)))$$,
        $$CHECK ((((kind = 'voice'::text) AND (lane = 'voice'::text)) OR ((kind = 'matte'::text) AND (lane = 'matte'::text)) OR ((kind = ANY (ARRAY['photo'::text, 'asset_candidate'::text])) AND (lane = 'image'::text))))$$),
    ('studio.media_jobs'::regclass, 'media_jobs_check2',
        $$CHECK ((((kind = 'asset_candidate'::text) AND (board_id IS NULL) AND (story_id IS NULL)) OR ((kind <> 'asset_candidate'::text) AND (board_id IS NOT NULL) AND (story_id IS NOT NULL) AND (production_id IS NOT NULL))))$$,
        $$CHECK ((((kind = ANY (ARRAY['asset_candidate'::text, 'matte'::text])) AND (board_id IS NULL) AND (story_id IS NULL)) OR ((NOT (kind = ANY (ARRAY['asset_candidate'::text, 'matte'::text]))) AND (board_id IS NOT NULL) AND (story_id IS NOT NULL) AND (production_id IS NOT NULL))))$$)
), definitions AS (
    SELECT bool_and(COALESCE(c.convalidated AND pg_get_constraintdef(c.oid) = v12, false)) AS complete_0012,
           bool_and(COALESCE(c.convalidated AND pg_get_constraintdef(c.oid) = COALESCE(v13, v12), false)) AS complete_0013
    FROM expected
    LEFT JOIN pg_constraint AS c ON c.conrelid = relation AND c.conname = name
), release_column AS (
    SELECT atttypid = 'smallint'::regtype AND attnotnull
        AND pg_get_expr(adbin, adrelid) IN ('0', '''0''::smallint')
        AND EXISTS (
            SELECT FROM aclexplode(attacl) AS acl JOIN pg_roles ON oid = acl.grantee
            WHERE rolname = 'studio_app' AND acl.privilege_type = 'UPDATE'
        ) AS verified
    FROM pg_attribute
    LEFT JOIN pg_attrdef ON adrelid = attrelid AND adnum = attnum
    WHERE attrelid = 'studio.media_jobs'::regclass
      AND attname = 'matte_releases' AND NOT attisdropped
)
SELECT CASE
    WHEN current_setting('transaction_read_only') <> 'on' OR pg_is_in_recovery() THEN 'unverified'
    -- The validated 0012 media_job_lanes check already excludes a matte FIFO row.
    WHEN heads = ARRAY['0012_release_constraints']::varchar[] AND complete_0012
         AND NOT EXISTS (SELECT FROM release_column)
        THEN '0012_release_constraints'
    WHEN heads = ARRAY['0013_matte_jobs']::varchar[] AND complete_0013
         AND EXISTS (SELECT FROM release_column WHERE verified)
         AND EXISTS (SELECT FROM studio.media_job_lanes WHERE lane = 'matte' AND next_position >= 1)
        THEN '0013_matte_jobs'
    ELSE 'unverified'
END
FROM revision CROSS JOIN definitions;
