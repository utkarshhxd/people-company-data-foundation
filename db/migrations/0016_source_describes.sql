-- What is this source cataloguing: organisations, or places?
--
-- The question sounds academic until two vendors produce identical evidence and
-- the right answer differs:
--
--   * 925 entities named "UnitedHealth Group" sharing unitedhealthgroup.com came
--     from Apollo person rows naming an employer. One company. Its employees sit
--     in many cities, and these must resolve together.
--   * 156 entities named "Subway Sandwiches & Salads" sharing subway.com came
--     from a business directory listing premises. Different franchises, different
--     owners, different addresses. Merging them would erase all but one.
--
-- Same name, same domain, opposite correct answers. Nothing in the row
-- distinguishes them, and no threshold can: the distinguishing fact is what the
-- vendor set out to catalogue, which is a property of the source.
--
-- So it is recorded on the source, once, by whoever loads it -- rather than
-- guessed per record forever.
--
-- 'organisation' is the default because it is the existing behaviour: every
-- source loaded before this migration was resolved on the assumption that a
-- shared domain means a shared company. Changing what stored data means as a
-- side effect of a migration would be worse than leaving it to be set
-- deliberately.

ALTER TABLE source
    ADD COLUMN describes text NOT NULL DEFAULT 'organisation'
        CHECK (describes IN ('organisation', 'location'));

COMMENT ON COLUMN source.describes IS
    'organisation: rows name companies/employers, so a shared domain implies a '
    'shared entity. location: rows name premises, so a shared domain implies '
    'only a shared brand and cannot carry a link on its own.';
