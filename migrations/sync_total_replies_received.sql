-- Keep creators.total_replies_received in sync with pipeline reply stages.
-- Safe to re-run. Used by lifecycle "winner" state.

UPDATE creators c
SET total_replies_received = sub.n
FROM (
  SELECT creator_id, COUNT(*)::int AS n
  FROM creator_pipeline
  WHERE stage IN (
    'replied', 'won', 'received', 'success', 'responded', 'accepted', 'shipped'
  )
  GROUP BY creator_id
) sub
WHERE c.id = sub.creator_id
  AND COALESCE(c.total_replies_received, 0) <> sub.n;
