import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** Presentation only: preserve the fetched source and its exact reference for submission. */
export function HrLoopMethodPreview({ content }: { content: string }) {
  const prose = content.replace(
    /^\uFEFF?---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/,
    "",
  );
  return (
    <div className="message-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) =>
            href && /^https?:\/\//i.test(href) ? (
              <a href={href} rel="noreferrer noopener">
                {children}
              </a>
            ) : (
              <>{children}</>
            ),
          table: ({ node: _node, ...props }) => (
            <div className="table-scroll">
              <table {...props} />
            </div>
          ),
        }}
      >
        {prose}
      </ReactMarkdown>
    </div>
  );
}
