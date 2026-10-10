// Exercise the actual renderer against partial, uncertain and corrected evidence.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/app.js', 'utf8');
const renderer = source.slice(source.indexOf('  function renderBrokerOrders('), source.indexOf('  async function updatePaperResearch('));
assert(renderer.includes('function renderBrokerOrders'));
const target = {innerHTML:''};
const context = vm.createContext({$:()=>target, shortTs:x=>x||'', fmtMoney:x=>`$${x}`,
  escapeHtml:x=>String(x).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;')});
vm.runInContext(renderer, context);
function render(pending, fills) {
  context.data = {ledger:{pending_broker_orders:pending,broker_fills:fills}};
  vm.runInContext('renderBrokerOrders(data)', context);
  return target.innerHTML;
}
const order = {order_id:'1',broker:{order:{shares:10}},signal:{id:'s1',ticker:'TEST',side:'buy'}};
const fill = {order_id:'1',ticker:'TEST',shares:4,price:100,confirmed:true};
let html = render([order],[fill]);
assert(html.includes('bo-list') && html.includes('bo-row'));
assert.equal((html.match(/bo-row/g)||[]).length, 1); // one order row, no duplicate fill
assert(html.includes('4 / 10') || (html.includes('>4<') && html.includes('10')));
assert(html.includes('left 6') || html.includes('6'));
assert(html.includes('$100'));
assert(html.includes('Partial fill · remainder tracked') && html.includes('data-cancel-review="s1"'));
assert(html.includes('bo-side-badge buy') && html.includes('BUY'));
assert(html.includes('bo-fill-bar'));
html = render([order],[{...fill,shares:10}]);
assert(html.includes('Filled · awaiting terminal confirmation'));
html = render([],[{...fill,confirmed:false,broker_reconciled:true}]);
assert(html.includes('Fill unverified') && !html.includes('$100'));
html = render([],[{...fill,confirmed:false,broker_reconciled:true,broker_fill_state:'voided'}]);
assert(html.includes('Execution reversed') && !html.includes('$100'));
html = render([{...order,order_id:'intent:1',signal:{...order.signal,ticker:'<script>'}}],[]);
assert(html.includes('&lt;script>') && !html.includes('data-cancel-review='));
console.log('broker order UI: deduplication, partial quantities, uncertainty, corrections and escaped content passed');
