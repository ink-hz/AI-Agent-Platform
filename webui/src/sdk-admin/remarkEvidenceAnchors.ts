import type { Root, RootContent } from 'mdast';

// Frozen knowledge evidence anchors use the KB namespace. Accept only an
// attribute-free, empty <a id="…"></a> pair; leave all other HTML to skipHtml.
const openingAnchor = /^<a id="(KB-[A-Za-z0-9][A-Za-z0-9._-]{0,156})">$/;

export function remarkEvidenceAnchors() {
    return (tree: Root) => {
        function visit(node: Root | RootContent) {
            if (!('children' in node)) return;
            if (node.type === 'paragraph') {
                for (let i = 0; i < node.children.length - 1; i++) {
                    const start = node.children[i], end = node.children[i + 1];
                    if (start.type !== 'html' || end.type !== 'html' || end.value !== '</a>') continue;
                    const match = openingAnchor.exec(start.value);
                    if (match) {
                        // Build a safe HAST span through Markdown data, never raw HTML.
                        node.children.splice(i, 2, { type: 'text', value: '', data: { hName: 'span', hProperties: { id: match[1] } } });
                    }
                }
            }
            node.children.forEach(visit);
        }
        visit(tree);
    };
}
