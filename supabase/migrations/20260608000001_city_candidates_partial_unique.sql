-- Fix: PostgreSQL UNIQUE(source, source_id, agent_id) does not prevent
-- duplicate rows when agent_id IS NULL (NULL != NULL).
-- Add a partial unique index to cover city-scraped candidates.

-- Step 1: Remove pre-existing duplicates, keeping the oldest row (smallest id)
DELETE FROM property_candidates
WHERE agent_id IS NULL
  AND id NOT IN (
    SELECT MIN(id)
    FROM property_candidates
    WHERE agent_id IS NULL
    GROUP BY source, source_id
  );

-- Step 2: Create partial unique index to prevent future duplicates
CREATE UNIQUE INDEX IF NOT EXISTS uq_property_candidates_null_agent
    ON property_candidates (source, source_id)
    WHERE agent_id IS NULL;
