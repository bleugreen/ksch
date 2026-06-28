const fs = require("fs");
const ELK = require("elkjs/lib/elk.bundled.js");

async function main() {
  const input = JSON.parse(fs.readFileSync(0, "utf8"));
  const elk = new ELK();
  const results = [];
  for (const graph of input.graphs || []) {
    const layout = await elk.layout(graph);
    const edges = {};
    for (const edge of layout.edges || []) {
      edges[edge.id] = [];
      for (const section of edge.sections || []) {
        const points = [];
        if (section.startPoint) {
          points.push([section.startPoint.x || 0, section.startPoint.y || 0]);
        }
        for (const bend of section.bendPoints || []) {
          points.push([bend.x || 0, bend.y || 0]);
        }
        if (section.endPoint) {
          points.push([section.endPoint.x || 0, section.endPoint.y || 0]);
        }
        if (points.length >= 2) {
          edges[edge.id].push(points);
        }
      }
    }
    results.push({
      id: graph.id,
      width: layout.width || 0,
      height: layout.height || 0,
      nodes: Object.fromEntries((layout.children || []).map((node) => [node.id, { x: node.x || 0, y: node.y || 0 }])),
      edges,
    });
  }
  process.stdout.write(JSON.stringify({ results }) + "\n");
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
