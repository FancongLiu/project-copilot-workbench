# Obsidian-style evidence graph selection

Date: 2026-07-19

## Reference-site identification

The supplied [jaschen notes page](https://notes.jaschen.life/(2)%E8%B5%84%E6%BA%90/%E6%96%87%E6%91%98%E5%92%8C%E8%A7%82%E7%82%B9/AI+%E4%BA%A7%E5%93%81%E6%9D%82%E8%B0%88)
is an Obsidian Publish site, not a separately implemented graph application.
The inspected page loads the Obsidian Publish application from
`publish.obsidian.md`, displays `Powered by Obsidian Publish`, and exposes the
same two-level interaction documented by Obsidian: a local graph around the
current note and a full graph for the complete vault.

Primary references:

- [Obsidian Graph view documentation](https://help.obsidian.md/plugins/graph)
- [Obsidian Publish documentation](https://help.obsidian.md/publish)

## GitHub alternatives checked

- [Quartz](https://github.com/jackyzha0/quartz) provides an open-source digital
  garden with local/global graph behavior. It is a complete publishing stack,
  which would duplicate Project Copilot's existing Web application.
- [Cytoscape.js](https://github.com/cytoscape/cytoscape.js) is a mature MIT graph
  visualization library and was already vendored by Project Copilot.
- [Sigma.js](https://github.com/jacomyal/sigma.js) and
  [force-graph](https://github.com/vasturiano/force-graph) are strong graph
  renderers, but adding either would create a second renderer and another
  dependency without improving the current small project graph.

## Decision

Reuse Cytoscape.js. Implement the useful Obsidian interaction rather than copy
Obsidian Publish itself:

1. no graph before a question uses evidence;
2. a compact local graph showing the project → directory → cited-file path;
3. highlighted nodes for the evidence used in the latest answer;
4. full-project expansion only when the engineer asks for it;
5. original human filenames as terminal node labels;
6. no graph database, new publishing platform or second visualization engine.

This keeps the graph a transparent evidence-navigation aid instead of a
decorative dashboard that competes with the Chat workflow.
