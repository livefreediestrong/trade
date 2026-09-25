"""Exercise workspace boundaries and approval events without any broker or network."""
from pathlib import Path
import re
import subprocess

SOURCE = (Path(__file__).parents[1] / "static" / "app.js").read_text(encoding="utf-8")


def function(name):
    match = re.search(r"^  (?:async )?function " + name + r"\(", SOURCE, re.M)
    assert match, name
    end = SOURCE.index("\n  }", match.start()) + len("\n  }")
    return SOURCE[match.start():end]


def run_js(names, body):
    code = "const assert = require('node:assert/strict');\n"
    names = list(names)
    # Shared UI helpers called by renderers (e.g. the beginner pulse strip).
    for helper in ("syncBeginnerPulse",):
        if helper not in names and any(helper + "(" in function(n) for n in names):
            names.append(helper)
    code += "\n".join(function(name) for name in names) + "\n" + body
    result = subprocess.run(["node", "-e", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_broker_pnl_distinguishes_reported_zero_from_missing_daily_feed():
    run_js(['renderBrokerBook'], r"""
const nodes = new Map();
const $ = selector => { if(!nodes.has(selector)) nodes.set(selector,{textContent:'',innerHTML:'',hidden:false,classList:{add(){},remove(){},toggle(){}}}); return nodes.get(selector); };
const window={__brokerStatus:{broker:'ibkr'}};
const fmtMoney = value => '$'+Number(value).toFixed(2);
const fmtSigned = fmtMoney;
const escapeHtml = value => String(value);
const book={ok:true,paper_mode:false,equity:110,cash:110,buying_power:110,day_pnl_usd:null,risk_error:'No daily feed',
 account_window_pnl:{realized:0,unrealized:null},pnl_diagnostics:{status:'waiting',callbacks:0,retries:2,waiting_seconds:125,last_update_age_seconds:null}};
renderBrokerBook({broker_book:book});
assert.match($('#broker-book-pnl-detail').textContent,/realized \$0.00 · unrealized unavailable/);
assert.match($('#broker-book-pnl-detail').textContent,/different basis from Daily P&L/);
assert.match($('#broker-book-day').textContent,/Daily P&L unavailable/);
assert.match($('#broker-book-pnl-feed').textContent,/0 update\(s\).*2 automatic retry\(s\).*125s/);
book.day_pnl_usd=0;
renderBrokerBook({broker_book:book});
assert.match($('#broker-book-day').textContent,/Today \$0.00/);
book.ok=false;
renderBrokerBook({broker_book:book});
assert.equal($('#broker-book-pnl-detail').hidden,true);
assert.equal($('#broker-book-pnl-feed').hidden,true);
""")


def test_broker_pnl_on_change_distinguishes_value_age_from_gateway_responsiveness():
    run_js(['renderBrokerBook'], r"""
const nodes = new Map();
const $ = selector => { if(!nodes.has(selector)) nodes.set(selector,{textContent:'',innerHTML:'',hidden:false,classList:{add(){},remove(){},toggle(){}}}); return nodes.get(selector); };
const window={__brokerStatus:{broker:'ibkr'}};
const fmtMoney = value => '$'+Number(value).toFixed(2);
const fmtSigned = fmtMoney;
const escapeHtml = value => String(value);
const book={ok:true,paper_mode:false,equity:110,cash:110,buying_power:110,day_pnl_usd:0,risk_ready:true,risk_error:null,
 pnl_diagnostics:{status:'ready',update_mode:'on_change',callbacks:1,retries:0,waiting_seconds:182,last_update_age_seconds:180,api_response_age_seconds:2}};
const before=JSON.stringify(book);
renderBrokerBook({broker_book:book});
assert.match($('#broker-book-day').textContent,/Today \$0.00/);
assert.match($('#broker-book-pnl-feed').textContent,/active subscription · value updates on change/);
assert.match($('#broker-book-pnl-feed').textContent,/last value update 180s ago · Gateway responded 2s ago/);
assert.doesNotMatch($('#broker-book-pnl-feed').textContent,/stale|unavailable|waiting/);
assert.equal($('#broker-book-risk').hidden,true);
assert.equal(JSON.stringify(book),before);

book.day_pnl_usd=null;
book.risk_ready=false;
book.risk_error='Daily P&L has not arrived; new live risk is blocked.';
Object.assign(book.pnl_diagnostics,{status:'waiting',callbacks:0,last_update_age_seconds:null,waiting_seconds:65});
renderBrokerBook({broker_book:book});
assert.match($('#broker-book-day').textContent,/Daily P&L unavailable/);
assert.match($('#broker-book-pnl-feed').textContent,/waiting for first Gateway value/);
assert.match($('#broker-book-pnl-feed').textContent,/waiting 65s · Gateway responded 2s ago/);
assert.doesNotMatch($('#broker-book-pnl-feed').textContent,/active subscription|last value update/);
assert.equal($('#broker-book-risk').textContent,book.risk_error);
assert.equal($('#broker-book-risk').hidden,false);

book.risk_error='Broker connection is recovering; new live risk is blocked.';
Object.assign(book.pnl_diagnostics,{status:'recovering',api_response_age_seconds:null});
renderBrokerBook({broker_book:book});
assert.match($('#broker-book-pnl-feed').textContent,/recovering; waiting for a new Gateway value|Recovering Daily P&L/);
assert.match($('#broker-book-pnl-feed').textContent,/Gateway response not yet confirmed/);
assert.doesNotMatch($('#broker-book-pnl-feed').textContent,/active subscription|Gateway responded/);
assert.match($('#broker-book-day').textContent,/Daily P&L unavailable/);
assert.equal($('#broker-book-risk').textContent,book.risk_error);
assert.equal($('#broker-book-risk').hidden,false);
""")


def test_unified_paper_queue_uses_own_status_and_keeps_rejected_call_as_research():
    run_js(["signalWorkspace", "workspaceView", "renderUnifiedPaper", "quoteLabel"], r"""
let activeWorkspace='live';
const nodes = new Map();
const $ = selector => { if(!nodes.has(selector)) nodes.set(selector,{value:'',innerHTML:'',textContent:''}); return nodes.get(selector); };
const signalCard = s => `<article>${s.id}:${s.workspace}</article>`;
const data = {config:{mode:'live_manual',paper_auto_approve:true},
 signals:{pending:[{id:'live',workspace:'live'},{id:'paper',workspace:'paper'}], rejected:[{id:'skip',workspace:'paper'}]},
 paper_desk_call:{ticker:'TEST',side:'sell',decision:'hold',status:'rejected',actionable:true,reject_reason:'risk budget below one share',llm_thesis:'Underlying bearish thesis'}};
const before=JSON.stringify(data);
renderUnifiedPaper(data);
assert.equal($('#paper-signal-list').innerHTML,'<article>paper:paper</article>');
assert.match($('#paper-latest-call').textContent,/^Research SELL TEST · rejected · risk budget below one share/);
assert.doesNotMatch($('#paper-latest-call').textContent,/Eligible paper idea/);
$('#paper-signal-status').value='rejected';
renderUnifiedPaper(data);
assert.equal($('#paper-signal-list').innerHTML,'<article>skip:paper</article>');
assert.equal(activeWorkspace,'live');
assert.equal(JSON.stringify(data),before);
""")


def test_ten_dollar_readiness_never_claims_market_orders_are_capped():
    run_js(["liveTestChecks"], r"""
const now=100000;
const data={config:{mode:'live_manual'},loop:{rth_ok:true,outside_rth:false},broker:{connected:true},
 broker_book:{ok:true,paper_mode:false,account_id:'TEST',risk_ready:true,day_pnl_usd:0},ledger:{pending_broker_orders:[]}};
const before=JSON.stringify(data);
let reasons=liveTestChecks(data,now,now);
assert.equal(reasons.length,2);
assert.match(reasons.join(' '),/cannot enforce a \$10/);
assert.match(reasons.join(' '),/estimates, not guaranteed costs/);
for(const pnl of [null,undefined,NaN,Infinity,'0']) {
 reasons=liveTestChecks({...data,broker_book:{...data.broker_book,day_pnl_usd:pnl}},now,now);
 assert.match(reasons.join(' '),/Daily broker P&L is unavailable/);
}
assert.match(liveTestChecks({...data,loop:{rth_ok:false}},now,now).join(' '),/hours are closed/);
assert.match(liveTestChecks({...data,loop:{}},now,now).join(' '),/session status is unavailable/);
assert.match(liveTestChecks(data,now-26000,now).join(' '),/stale or unavailable/);
assert.match(liveTestChecks({...data,ledger:{pending_broker_orders:[{}]}},now,now).join(' '),/reconciliation/);
assert.match(liveTestChecks({...data,broker_book:{...data.broker_book,paper_mode:true}},now,now).join(' '),/actual live account/);
assert.equal(JSON.stringify(data),before);
""")


def test_unified_template_keeps_money_controls_in_separate_sections():
    from html.parser import HTMLParser

    class Layout(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack, self.ids, self.owners = [], set(), {}

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            identifier = attrs.get('id')
            if identifier:
                assert identifier not in self.ids, f'duplicate id: {identifier}'
                self.ids.add(identifier)
                self.owners[identifier] = tuple(self.stack)
            if tag not in {'input', 'br', 'hr', 'meta', 'link', 'img', 'source', 'wbr'}:
                self.stack.append(identifier)

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)

        def handle_endtag(self, tag):
            self.stack.pop()

    layout = Layout()
    from jinja2 import Environment, FileSystemLoader, StrictUndefined
    templates = Path(__file__).parents[1] / 'templates'
    env = Environment(loader=FileSystemLoader(templates), undefined=StrictUndefined, autoescape=True)
    layout.feed(env.get_template('index.html').render(banner='Test desk', config={'COMPANION_AVAILABLE':True,'OPTIONS_AVAILABLE':True}))
    for identifier in ('options-form','option-confirm','option-book-status'):
        assert 'desk-options' in layout.owners[identifier]
        assert 'desk-live' not in layout.owners[identifier]
    # A running older backend can continue to render the hot-reloaded template.
    env.get_template('index.html').render(banner='Test desk',config={'COMPANION_AVAILABLE':True})
    for identifier in ('loop-gates', 'loop-strip', 'loop-pnl', 'loop-goal', 'paper-experiments'):
        assert 'desk-paper' in layout.owners[identifier]
    for identifier in ['paper-auto-approve', 'paper-risk-preset', 'paper-friction', 'paper-signal-list', 'positions', 'fills', 'report-body']:
        assert 'desk-paper' in layout.owners[identifier], identifier
        assert 'desk-live' not in layout.owners[identifier], identifier
    for identifier in ['broker-book', 'broker-orders-list', 'signal-list', 'live-test-status']:
        assert 'desk-live' in layout.owners[identifier], identifier
    assert 'btn-ui-simple' not in layout.ids
    assert 'btn-ui-advanced' not in layout.ids


def test_paper_headline_and_provider_label_do_not_take_live_values():
    run_js(["renderTop", "getUiMode"], r"""
const nodes=new Map();
const $=selector=>{if(!nodes.has(selector))nodes.set(selector,{value:'',textContent:'',dataset:{},style:{},classList:{add(){},remove(){},toggle(){},contains(){return false;}},closest(){return null;},removeAttribute(){},setAttribute(){}}); return nodes.get(selector);};
const $$=()=>[];
const document={activeElement:null,getElementById:()=>null,body:{classList:{contains(){return false;}}}};
let lastHeroPnl=null;
const fmtMoney=n=>'$'+n;
const tweenMoney=(el,n)=>{el.textContent=String(n);};
const renderStartupStatus=()=>{},brokerMode=()=>true,updateStartEnabled=()=>{},renderSessionChip=()=>{},syncFillModeToggle=()=>{},renderPresetDetail=()=>{},renderWatchlistCount=()=>{},renderBrokerStatus=()=>{};
const data={config:{mode:'live_manual',risk_preset:'high'},daily:{pnl:999,trades:99},daily_recap:{headline:'LIVE'},
 paper_daily:{pnl:-2,trades:3},paper_daily_recap:{market_closed:true,headline:'PAPER'},ledger:{equity:1000,cash:1000},llm:{brain_mode:'claude',configured:true,model:'claude-test'}};
renderTop(data);
assert.equal($('#stat-pnl').textContent,'-2');
assert.equal($('#stat-trades').textContent,'3');
assert.equal($('#daily-recap-headline').textContent,'PAPER');
assert.equal($('#stat-llm').textContent,'Claude · claude-test');
delete data.llm;
renderTop(data);
assert.equal($('#stat-llm').textContent,'Checking research model…');
data.llm={brain_mode:'gemini',configured:false};
renderTop(data);
assert.equal($('#stat-llm').textContent,'Gemini needs an API key');
data.llm={brain_mode:'jev',configured:false,jev_key_present:true,selection_error:'JEV is locked until repaired.'};
renderTop(data);
assert.equal($('#stat-llm').textContent,'JEV unavailable');
assert.equal($('#stat-llm').title,'JEV is locked until repaired.');
assert.equal($('#stat-mode').textContent,'Approve each live broker order');
assert.equal($('#mode-select').value,'live_manual');
""")


def test_workspace_filters_preserve_live_config_and_canonical_rows():
    run_js(["signalWorkspace", "workspaceView", "deskCall", "firstPending", "isOppActionable"], r"""
let activeWorkspace = 'live';
const live = {id:'live',workspace:'live',source:'pending',actionable:true};
const paper = {id:'paper',workspace:'paper',source:'pending',actionable:true};
const legacyPaper = {id:'old',mode_at_create:'auto_paper',actionable:true};
const state = {config:{mode:'live_manual',session_active:true,paper_research_enabled:true,paper_auto_approve:true},
 signals:{pending:[live,paper,legacyPaper],approved:[]}, opportunities:[paper,live],
 desk_call:{ticker:'LIVE'}, paper_desk_call:{ticker:'PAPER'}};
const original = JSON.stringify(state);
assert.deepEqual(workspaceView(state).signals.pending.map(s=>s.id), ['live']);
assert.equal(firstPending().id,'live');
assert.equal(deskCall(state).ticker,'LIVE');
activeWorkspace = 'paper';
assert.deepEqual(workspaceView(state).signals.pending.map(s=>s.id), ['paper','old']);
assert.equal(workspaceView(state).config.mode,'auto_paper');
assert.equal(firstPending().id,'paper');
assert.equal(deskCall(state).ticker,'PAPER');
assert.equal(JSON.stringify(state), original);
assert.equal(signalWorkspace({mode_at_create:'manual'}),'paper');
assert.equal(signalWorkspace({}),'live');
""")


DOM = r"""
let activeWorkspace='live', pendingApproveId=null, approvalContext=null;
let approveReturnFocus=null, approveReturnSignalId=null;
const nodes = new Map();
const $ = selector => {
 if (!nodes.has(selector)) {
  const classes = new Set(selector === '#approve-modal' ? ['hidden'] : []);
  nodes.set(selector,{dataset:{},value:'',hidden:false,disabled:false,checked:false,innerHTML:'',textContent:'',listeners:{},
   classList:{add:v=>classes.add(v),remove:v=>classes.delete(v),contains:v=>classes.has(v),toggle(v,on){if(on===undefined)on=!classes.has(v);on?classes.add(v):classes.delete(v);return on;}},
   setAttribute(){},focus(){document.activeElement=this;},closest(){return null;},
   querySelectorAll(){return [$('#live-ack-input'),$('#btn-approve-cancel')];},
   addEventListener(event,handler){(this.listeners[event] ||= []).push(handler);},
   dispatch(event){for(const listener of this.listeners[event] || []) listener({currentTarget:this});}});
 }
 return nodes.get(selector);
};
const $$ = () => [$('#approve-stop'),$('#approve-tp'),$('#approve-trail'),$('#approve-no-bracket')];
global.document={activeElement:null,getElementById:()=>null};
global.window={__brokerStatus:{broker:'ibkr',configured:true,paper_mode:false}};
const state={config:{mode:'live_manual',paper_risk_preset:'mid'},presets:{mid:{stop_r:1,target_r:2.5}},ledger:{cash:1000},daily:{},preset:{}};
let signal={id:'one',ticker:'TEST',workspace:'live',side:'buy',source:'pending',actionable:true,suggested_shares:1,signal_price:100,
 quote:{source:'Test',market_time:new Date().toISOString(),fresh:true},llm_model:'test-model'};
const findSignalById=()=>signal;
const requests=[];
const api=async path=>{requests.push(path);return {order:{type:'limit',limit:100,shares:1,notional_bound:100},broker:window.__brokerStatus,review_token:'ticket',identity:{account_id:'TEST'},expires_at:new Date(Date.now()+90000).toISOString()};};
const renderBrokerStatus=b=>{window.__brokerStatus=b;};
const toast=()=>{};
const plainify=s=>s;
const fmtMoney=n=>'$'+n;
const lastDataAt=Date.now();
"""


APPROVAL_FUNCTIONS = ["signalWorkspace", "signalModelLabel", "quoteLabel", "brokerMode", "realMoney", "moneyNoun", "escapeHtml", "approvalIsAllowed", "updateApproveEligibility", "approvalBody", "openApprovePreview", "closeApproveModal", "handleApproveKeydown"]


def test_first_open_ticker_input_and_keyboard_events_without_order_submission():
    registration = re.search(r'^  \$\("#live-ack-input"\)\?\.addEventListener\("input", updateApproveEligibility\);$', SOURCE, re.M)
    assert registration, "Acknowledgement listener must be registered at initialization"
    run_js(APPROVAL_FUNCTIONS, DOM + registration.group(0) + r"""
(async()=>{
 await openApprovePreview('one');
 assert.equal($('#btn-approve-confirm').disabled,true);
 assert.equal($('#approve-bracket').hidden,true);
 assert.match($('#approve-preview').innerHTML,/no attached broker stop-loss/);
 $('#live-ack-input').value='WRONG'; $('#live-ack-input').dispatch('input');
 assert.equal($('#btn-approve-confirm').disabled,true);
 $('#live-ack-input').value='test'; $('#live-ack-input').dispatch('input');
 assert.equal($('#btn-approve-confirm').disabled,false);
 assert.deepEqual(requests,['/api/signals/one/review']);
 let prevented=false;
 $('#btn-approve-cancel').focus();
 handleApproveKeydown({key:'Tab',shiftKey:false,preventDefault(){prevented=true;}});
 assert.equal(prevented,true); assert.equal(document.activeElement,$('#live-ack-input'));
 handleApproveKeydown({key:'Escape',preventDefault(){}});
 assert.equal($('#approve-modal').classList.contains('hidden'),true);
 assert.equal(pendingApproveId,null);
 assert.equal(approvalContext,null);
})().catch(e=>{console.error(e);process.exit(1)});
""")


def test_paper_candidate_opens_simulation_even_in_live_workspace_without_identity_request():
    run_js(APPROVAL_FUNCTIONS, DOM + r"""
(async()=>{
 signal.workspace='paper';
 await openApprovePreview('one');
 assert.equal(activeWorkspace,'live');
 assert.equal(state.config.mode,'live_manual');
 assert.equal(approvalContext.workspace,'paper');
 assert.equal(approvalContext.live,false);
 assert.equal($('#approve-modal-title').textContent,'Confirm paper trade');
 assert.match($('#approve-preview').innerHTML,/SIMULATED FUNDS/);
 assert.doesNotMatch($('#approve-preview').innerHTML,/REAL MONEY/);
 assert.equal($('#approve-bracket').hidden,false);
 assert.equal($('#btn-approve-confirm').disabled,false);
 assert.deepEqual(requests,[]);
})().catch(e=>{console.error(e);process.exit(1)});
""")


def test_acknowledgement_does_not_override_staleness_and_broker_body_has_no_paper_exits():
    run_js(["approvalIsAllowed", "approvalBody"], r"""
const now=Date.now(), context={workspace:'live',live:true,ticker:'TEST',quote:{market_time:new Date(now).toISOString(),fresh:true}};
assert.equal(approvalIsAllowed(context,'TEST',now,now),true);
assert.equal(approvalIsAllowed(context,'TEST',now-26000,now),false);
assert.equal(approvalIsAllowed({...context,quote:{market_time:new Date(now-121000).toISOString(),fresh:true}},'TEST',now,now),false);
assert.equal(approvalIsAllowed({...context,expires_at:new Date(now-1000).toISOString()},'TEST',now,now),false);
const fields={stop:'1%',target:'3%',trail:'2',noBracket:false};
assert.deepEqual(approvalBody({...context,review_token:'ticket'},{...fields,ackTicker:'TEST'}),{review_token:'ticket',ack_ticker:'TEST'});
assert.deepEqual(approvalBody({workspace:'paper'},fields),{stop_loss:'1%',take_profit:'3%',trail_pct:'2'});
assert.deepEqual(approvalBody({workspace:'paper'},{noBracket:true}),{bracket_off:true,no_bracket:true});
""")


def test_material_broker_and_paper_snapshot_changes_are_not_deduplicated():
    run_js(["liteSignature"], r"""
const a={broker_book:{ok:true,positions:[{ticker:'AAPL',shares:100,last:100}],day_pnl_usd:null}};
const b={broker_book:{ok:true,positions:[{ticker:'AAPL',shares:1,last:101}],day_pnl_usd:null}};
assert.notEqual(liteSignature(a),liteSignature(b));
assert.notEqual(liteSignature({broker_book:{ok:false,positions:[]}}),liteSignature({broker_book:{ok:true,positions:[]}}));
assert.notEqual(liteSignature({config:{paper_auto_approve:false}}),liteSignature({config:{paper_auto_approve:true}}));
assert.notEqual(liteSignature({paper_ledger:{fills:[]}}),liteSignature({paper_ledger:{fills:[{id:'new'}]}}));
""")


def test_review_cannot_submit_after_account_switch():
    run_js(APPROVAL_FUNCTIONS + ["confirmApprove"], DOM + r"""
(async()=>{
 state.config.broker_identity={account_id:'TEST'};
 await openApprovePreview('one');
 assert.match($('#approve-preview').innerHTML,/TEST/);
 $('#live-ack-input').value='TEST';
 state.config.broker_identity={account_id:'OTHER'};
 await confirmApprove();
 assert.deepEqual(requests,['/api/signals/one/review']);
 assert.equal(pendingApproveId,'one');
})().catch(e=>{console.error(e);process.exit(1)});
""")


def test_paper_daily_uses_independent_target_and_recap_without_live_fallback():
    run_js(["signalWorkspace", "workspaceView"], r"""
let activeWorkspace='paper';
const state={config:{mode:'live_manual',daily_profit_target_usd:50,paper_research_enabled:true},
 daily:{pnl:91,target_usd:50},daily_recap:{headline:'Main session'},
 paper_daily:{pnl:3,target_usd:null},paper_daily_recap:{headline:'Paper research'}};
const original=JSON.stringify(state);
const paper=workspaceView(state);
assert.equal(paper.config.daily_profit_target_usd,null);
assert.deepEqual(paper.daily,{pnl:3,target_usd:null});
assert.equal(paper.daily_recap.headline,'Paper research');
assert.equal(workspaceView(state,'live').daily.target_usd,50);
assert.equal(workspaceView(state,'live').config.daily_profit_target_usd,50);
assert.deepEqual(workspaceView({config:state.config,daily:state.daily}).daily,{});
assert.equal(JSON.stringify(state),original);
""")


def test_live_start_does_not_read_hidden_paper_money_and_paper_controls_use_own_endpoint():
    run_js(["brokerMode", "updateStartEnabled", "startLiveResearch", "updatePaperResearch"], r"""
let activeWorkspace='live';
const state={config:{mode:'live_manual',paper_research_enabled:false}};
const button={dataset:{},disabled:true};
const $=selector=>{assert.equal(selector,'#btn-start-session');return button;};
const requests=[];
const api=async(path,options)=>{requests.push({path,body:JSON.parse(options.body)});return {config:{paper_research_enabled:true}};};
const toast=()=>{};const refresh=async()=>{};const renderWorkspaceChrome=()=>{};
(async()=>{
 updateStartEnabled(); assert.equal(button.disabled,false);
 await startLiveResearch();
 assert.deepEqual(requests,[{path:'/api/session/start',body:{}}]);
 activeWorkspace='paper'; updateStartEnabled(); assert.equal(button.disabled,true);
 await startLiveResearch(); assert.equal(requests.length,1);
 await updatePaperResearch({enabled:true,auto_approve:true});
 assert.deepEqual(requests[1],{path:'/api/paper-research',body:{enabled:true,auto_approve:true}});
 assert.equal(state.config.mode,'live_manual');
 assert.equal(state.config.paper_research_enabled,true);
})().catch(e=>{console.error(e);process.exit(1)});
""")


def test_live_risk_display_uses_broker_values_and_never_paper_equity_or_results():
    run_js(["renderRiskCockpit"], r"""
const nodes=new Map();
const $=selector=>{if(!nodes.has(selector))nodes.set(selector,{textContent:''});return nodes.get(selector);};
const fmtMoney=n=>'$'+n;
const paper={risk_cockpit:{exposure_usd:999,permission_label:'Paper limits clear'},execution_realism:{avg_slippage_bps:55},promotion_gate:{eligible:true},ledger:{equity:999999}};
renderRiskCockpit({...paper,broker:{paper_mode:false},broker_book:{ok:false,error:'Account data missing'}});
assert.equal($('#risk-exposure').textContent,'Unknown');
assert.equal($('#risk-slippage').textContent,'Unavailable');
assert.equal($('#risk-promotion').textContent,'Unavailable');
assert.match($('#risk-quality-note').textContent,/Account data missing/);
renderRiskCockpit({...paper,broker:{paper_mode:false},broker_book:{ok:true,positions:[{shares:2,last:100}],day_pnl_usd:4}});
assert.equal($('#risk-exposure').textContent,'$200');
assert.equal($('#risk-slippage').textContent,'$4');
assert.equal($('#risk-trade-allowed').textContent,'Order checks required');
""")


def test_compact_stream_preserves_history_clears_removed_rows_and_updates_risk():
    run_js(['liteSignature','applyStateLite'], r'''
let _liteSig='',fullStateSeen=false,stateLiteGeneration=0;
let state={config:{mode:'live_manual'},signals:{expired:[{id:'old'}],approved:[{id:'filled'}],pending:[{id:'pending'}]},ledger:{}};
const emitted=[];
const window={dispatchEvent:e=>emitted.push(JSON.parse(JSON.stringify(e.detail)))};
const CustomEvent=class{constructor(type,opts){this.type=type;this.detail=opts.detail;}};
const workspaceView=x=>x;
const renderStartupStatus=()=>{},renderOpenPnl=()=>{},renderBrokerBook=()=>{},syncThinking=()=>{},renderBleed=()=>{},renderBenchmark=()=>{},
 renderTop=()=>{},renderLoopPanel=()=>{},renderBrainLedger=()=>{},renderPaperChrome=()=>{},renderBuzz=()=>{},renderHeat=()=>{},renderRadar=()=>{},
 renderPace=()=>{},renderOpportunities=()=>{},renderPositions=()=>{},syncTradeGlowLedger=()=>{},updateDeskGuide=()=>{},scheduleSparks=()=>{},
 renderSignals=()=>{},renderFills=()=>{},renderBrokerStatus=()=>{},renderWorkspaceChrome=()=>{};
applyStateLite({ok:true,signal_history_delta:true,signals:{pending:[],approving:[],broker_pending:[]},broker:{paper_mode:false},
 broker_book:{day_pnl_usd:null,risk_ready:false},config:{mode:'live_manual',kill_switch:{armed:false}},desk_call:{ticker:'LATEST'}});
assert.deepEqual(state.signals.expired,[{id:'old'}]);assert.deepEqual(state.signals.pending,[]);
assert.equal(emitted.at(-1).broker.paper_mode,false);assert.equal(emitted.at(-1).desk_call.ticker,'LATEST');
assert.equal(emitted.at(-1).broker_book.day_pnl_usd,null);
applyStateLite({ok:true,signal_history_delta:true,signals:{expired:[],pending:[]},broker_book:{day_pnl_usd:0,risk_ready:true}});
assert.deepEqual(state.signals.expired,[]);assert.deepEqual(state.signals.approved,[{id:'filled'}]);assert.equal(state.broker_book.day_pnl_usd,0);
applyStateLite({ok:true,signals:{pending:[],approved:[],expired:[]},broker_book:{day_pnl_usd:null,risk_ready:false}});
assert.deepEqual(state.signals.approved,[]);assert.equal(state.broker_book.day_pnl_usd,null);
''')
