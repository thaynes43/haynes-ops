-- Read-only: the worst-case real previews for loadtest.py (30 most faces, 30 most OCR boxes,
-- 20 most elongated images). Output: "<kind> <score> <preview path>" per line.
with prev as (select "assetId", path from asset_file where type='preview'),
faces as (select f."assetId", count(*) n from asset_face f where f."deletedAt" is null group by 1 order by 2 desc limit 30),
ocr as (select o."assetId", count(*) n from asset_ocr o group by 1 order by 2 desc limit 30),
wide as (select e."assetId", greatest(e."exifImageWidth",e."exifImageHeight")::float/nullif(least(e."exifImageWidth",e."exifImageHeight"),0) r from asset_exif e join asset a on a.id=e."assetId" where a."deletedAt" is null and a.type='IMAGE' and e."exifImageWidth">0 and e."exifImageHeight">0 order by 2 desc nulls last limit 20)
select 'faces', n, p.path from faces x join prev p using ("assetId")
union all select 'ocr', n, p.path from ocr x join prev p using ("assetId")
union all select 'wide', round(r::numeric,1), p.path from wide x join prev p using ("assetId");
