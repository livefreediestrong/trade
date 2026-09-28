const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const nodes=new Map(),pending=[];
class Element {
 constructor(){this.handlers={};this.children=[];this.value='';this._html='';}
 set innerHTML(x){this._html=x;}get innerHTML(){return this._html;}
 set textContent(x){this._html=x;}get textContent(){return this._html;}
 addEventListener(event,fn){this.handlers[event]=fn;}
 append(x){this.children.push(x);}
 querySelector(){return new Element();}
}
const get=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
const context={document:{getElementById:get,createElement:()=>new Element()},
 IntersectionObserver:class{observe(){}disconnect(){}},
 fetch:(url,options)=>new Promise((resolve,reject)=>pending.push({url,options,resolve,reject}))};
const reply=d=>({ok:true,json:async()=>({ok:true,...d})});
(async()=>{
 vm.runInNewContext(fs.readFileSync('static/research_studio.js','utf8'),context);
 assert.equal(pending.length,0,'Opening the page must not request broker holdings');
 const button=get('portfolio-context-load'),result=get('portfolio-context-results');
 result.innerHTML='OLD ACCOUNT VALUES';
 const first=button.handlers.click({currentTarget:button});
 assert.equal(button.disabled,true);assert.doesNotMatch(result.innerHTML,/OLD ACCOUNT/);
 assert.equal(pending[0].url,'/api/research/portfolio');assert.equal(pending[0].options.method,'GET');
 pending[0].resolve(reply({summary:'0 of 1 valued',checked_at:'2026-09-27T12:00:00Z',
  coverage:{all_returned_asset_types:true,included_holdings:0,excluded_holdings:1},working_order_count:1,short_option_count:1,
  holdings:[{instrument:'<img src=x>',asset_type:'OPT',currency:'EUR',quantity:-1,value_usd:null,note:'Unvalued'}],
  gross_usd:null,net_usd:null,sectors:[],scenarios:[],note:'Not a complete account risk model.'}));
 await first;assert.equal(button.disabled,false);
 assert.match(result.innerHTML,/&lt;img src=x&gt;/);assert.doesNotMatch(result.innerHTML,/<img|\$0/);
 assert.match(result.innerHTML,/Unavailable/);assert.match(result.innerHTML,/not live trading quotes/);
 const failed=button.handlers.click({currentTarget:button});assert.doesNotMatch(result.innerHTML,/EUR/);
 pending[1].reject(Error('Connection unavailable'));await failed;
 assert.match(result.innerHTML,/amounts are unavailable/);assert.doesNotMatch(result.innerHTML,/\$|EUR/);
 const unavailable=button.handlers.click({currentTarget:button});
 pending[2].resolve(reply({status:'unavailable',error:'Gateway disconnected'}));await unavailable;
 assert.equal(result.innerHTML,'<p>Gateway disconnected</p>');
 console.log('Portfolio controller: explicit GET, pending/error clearing, unknown values, escaping and scope labels passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
