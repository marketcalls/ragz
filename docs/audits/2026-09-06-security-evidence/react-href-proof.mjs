import React from '/home/parshu/projects/ragz/frontend/node_modules/react/index.js';
import { renderToStaticMarkup } from '/home/parshu/projects/ragz/frontend/node_modules/react-dom/server.node.js';

for (const href of ['https://example.test/path', 'javascript:void(0)', 'data:text/plain,hello']) {
  const markup = renderToStaticMarkup(React.createElement('a', { href }, 'open'));
  console.log(JSON.stringify({ href, markup }));
}
