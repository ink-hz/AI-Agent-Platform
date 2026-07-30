import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";


export function MessageMarkdown({ content }: { content: string }) {
  return <div className="message-markdown">
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        a: ({ node: _node, ...props }) => <a {...props} rel="noreferrer noopener" />,
        table: ({ node: _node, ...props }) => <div className="table-scroll"><table {...props} /></div>,
      }}
    >{content}</ReactMarkdown>
  </div>;
}
