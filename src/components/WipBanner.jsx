/**
 * Work-in-progress notice.
 *
 * The platform is mid-rebuild and still holds DataHacks 2026 data, so anyone
 * who opens it should know what they are looking at before they trust a score
 * or an assignment. Set VITE_WIP_BANNER=off to hide it once the fall event
 * data is live and the event-namespacing migration has landed.
 */

const styles = `
  .wip-banner {
    display: flex; align-items: center; justify-content: center; gap: 9px;
    background: #FBF1DA; border-bottom: 1px solid #E8D5A8; color: #7A5A18;
    font-family: 'DM Sans', -apple-system, sans-serif;
    font-size: 12.5px; line-height: 1.45; padding: 8px 16px; text-align: center;
  }
  .wip-banner svg { flex-shrink: 0; }
  .wip-banner strong { font-weight: 650; }
  @media (max-width: 520px) { .wip-banner { font-size: 11.5px; padding: 7px 12px; } }
`;

export default function WipBanner() {
  if (import.meta.env.VITE_WIP_BANNER === "off") return null;

  return (
    <>
      <style>{styles}</style>
      <div className="wip-banner" role="status">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path d="M8 1.8 1.4 13.2h13.2L8 1.8Z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round"/>
          <path d="M8 6.4v3M8 11.4v.1" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
        </svg>
        <span>
          <strong>Work in progress.</strong> This platform is being rebuilt and still shows
          DataHacks 2026 data — assignments and scores here are not final.
        </span>
      </div>
    </>
  );
}
