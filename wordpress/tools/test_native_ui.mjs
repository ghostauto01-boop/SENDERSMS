#!/usr/bin/env node
/** Built UI in real Chromium against an explicitly synthetic REST fixture. Not native WP evidence. */
import zlib from 'node:zlib';import http from 'node:http';import fs from 'node:fs';import path from 'node:path';import assert from 'node:assert/strict';import {execFileSync} from 'node:child_process';
import puppeteer from 'puppeteer-core';import chromium from '@sparticuz/chromium';
const root=path.resolve(import.meta.dirname,'../..');const wp=path.join(root,'wordpress');
const docs=JSON.parse(execFileSync(path.join(wp,'tooling/node_modules/.bin/php-wasm-cli'),[path.join(wp,'tests/ui-browser-documents.php')],{cwd:root,env:{...process.env,PHP:'8.3'},maxBuffer:2000000}).toString());
let mode='pretty',requests=[],messages=new Map(),failFirst=true,assertions=0;const check=(condition,label)=>{assert.ok(condition,label);assertions++;};
const contact={id:1,first_name:'Ada',last_name:'Obi',phone_number:'+2348031234567',business_name:null,email:'ada@example.invalid',lead_status:'new',tags:[],has_consented:true,consent_status:'opted_in'};
const server=http.createServer(async(req,res)=>{
 try{
 const url=new URL(req.url,'http://127.0.0.1:4137');
 if(url.searchParams.get('sender_suite_app')==='1') {res.writeHead(200,docs[mode].headers);res.end(docs[mode].body);return;}
 const prefix='/sub/wp-content/plugins/sender-suite/app/';
 if(url.pathname.startsWith(prefix)){
  const relative=decodeURIComponent(url.pathname.slice(prefix.length));const file=path.resolve(wp,'sender-suite/app',relative);
  if(!file.startsWith(path.join(wp,'sender-suite/app')+path.sep)||!fs.existsSync(file)||!fs.statSync(file).isFile()){res.writeHead(404);res.end();return;}
  res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':file.endsWith('.svg')?'image/svg+xml':'application/octet-stream');res.end(fs.readFileSync(file));return;
 }
 const route=mode==='plain'?url.searchParams.get('rest_route')?.replace('/sender-suite/v1',''):url.pathname.replace('/sub/wp-json/sender-suite/v1','');
 const raw=await new Promise((resolve,reject)=>{let body='';req.on('data',b=>body+=b);req.on('end',()=>resolve(body));req.on('error',reject);});requests.push({method:req.method,route,url:url.href,body:raw,key:req.headers['idempotency-key']});
 const send=(status,body)=>{res.writeHead(status,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify(body));};
 if(route==='/auth/access')return send(200,{password_required:true,password_set:true,source:'admin',hint:'Synthetic native UI test password.'});
 if(route==='/auth/admin'){
  if(JSON.parse(raw).password!=='Synthetic-ui-password')return send(401,{detail:'Wrong password'});
  res.setHeader('Set-Cookie','fixture_session=valid; HttpOnly; SameSite=Lax; Path=/');return send(200,{success:true});
 }
 if(!req.headers.cookie?.includes('fixture_session=valid'))return send(401,{detail:'Not authenticated'});
 if(route==='/auth/me')return send(200,{id:1,username:'custom-native-operator',role:'admin',is_active:true});
 if(route==='/auth/logout'){res.setHeader('Set-Cookie','fixture_session=; Max-Age=0; Path=/');return send(200,{success:true});}
 if(route==='/contacts'||route==='/contacts/')return send(200,{items:[contact],total:1,page:1,per_page:20});
 if(route==='/contacts/tags/')return send(200,{items:[]});
 if(route==='/lists'||route==='/lists/')return send(200,{items:[],total:0,page:1});
 if(route==='/settings/appearance')return send(200,{});
 if(route==='/settings/gateway/phones')return send(200,{phones:[{id:'1',db_id:1,name:'Fixture explicit sender',sim_number:1,is_enabled:true,configured:true}]});
 if(route==='/send/history')return send(200,{items:[...messages.values()].map(m=>({...m,contact_name:'Ada Obi',body_preview:m.body,created_at:'2026-11-07T12:00:00Z'})),total:messages.size,page:1,per_page:20});
 if(route==='/send/'&&req.method==='POST'){
  let m=[...messages.values()].find(m=>m.key===req.headers['idempotency-key']);if(!m){m={id:messages.size+1,message_id:messages.size+1,status:'queued',provider_message_id:null,needs_review:false,key:req.headers['idempotency-key'],...JSON.parse(raw)};messages.set(m.id,m);}
  if(failFirst){failFirst=false;return send(503,{detail:'Synthetic lost acknowledgement; durable fixture row exists.'});}return send(202,m);
 }
 const match=route?.match(/^\/send\/messages\/(\d+)(\/cancel)?$/);
 if(match){const m=messages.get(Number(match[1]));if(!m)return send(404,{detail:'Not found'});if(match[2])m.status='cancelled';return send(200,m);}
 return send(404,{code:'rest_no_route',message:'Synthetic fixture has no implementation for this route.'});
 }catch(error){res.writeHead(500);res.end(JSON.stringify({detail:String(error)}));}
});
const libs=path.join(wp,'.cache/chromium-libs');fs.mkdirSync(libs,{recursive:true});const tar=path.join(wp,'.cache/chromium-libs.tar');fs.writeFileSync(tar,zlib.brotliDecompressSync(fs.readFileSync(path.join(root,'node_modules/@sparticuz/chromium/bin/al2023.tar.br'))));execFileSync('tar',['-xf',tar,'-C',libs]);
let browser;try{
 await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(4137,'127.0.0.1',resolve);});
 browser=await puppeteer.launch({executablePath:await chromium.executablePath(),args:chromium.args.filter(arg=>arg!=='--single-process'),headless:true,env:{...process.env,LD_LIBRARY_PATH:path.join(libs,'lib')+(process.env.LD_LIBRARY_PATH?':'+process.env.LD_LIBRARY_PATH:'')}});
 for(mode of ['pretty','plain','disabled']){
  requests=[];messages=new Map();failFirst=true;
  const context=await browser.createBrowserContext();const page=await context.newPage();const faults=[];
  page.on('pageerror',e=>faults.push(e.message));await page.setRequestInterception(true);page.on('request',r=>{if(!r.url().startsWith('http://127.0.0.1:4137/')&&!r.url().startsWith('data:'))r.abort();else r.continue();});
  await page.goto('http://127.0.0.1:4137/sub/?sender_suite_app=1#/login',{waitUntil:'networkidle0'});
  await page.waitForSelector('input[placeholder="Enter the admin password"]');await page.type('input[placeholder="Enter the admin password"]','Synthetic-ui-password');
  await page.evaluate(()=>{const button=[...document.querySelectorAll('button')].find(b=>b.textContent.includes('Sign in'));button.click();});
  await page.waitForFunction(()=>!location.hash.startsWith('#/login'));check(requests.some(r=>r.route==='/auth/admin'&&JSON.parse(r.body).password==='Synthetic-ui-password'),mode+' custom-username admin login');
  check(await page.evaluate(()=>!Object.values(sessionStorage).some(v=>v.includes('Synthetic-ui-password'))&&!Object.values(localStorage).some(v=>v.includes('Synthetic-ui-password'))),mode+' no persisted password');
  await page.evaluate(()=>{location.hash='/contacts?search=Ada';});await page.waitForFunction(()=>document.body.textContent.includes('Ada'));
  check(requests.some(r=>r.route==='/contacts'||r.route==='/contacts/'),mode+' contacts use native REST');
  await page.evaluate(()=>{location.hash='/send';});await page.waitForSelector('[aria-label="Native SMS queue"]');
  if(mode==='disabled'){check(await page.$eval('button[type="submit"]',el=>el.disabled),'default-off host disables compose');check(await page.evaluate(()=>document.body.textContent.includes('Sending is disabled')),'disabled boundary visible');check(!requests.some(r=>r.method==='POST'&&r.route==='/send/'),'disabled browser never submits');await context.close();continue;}
  const choose=async()=>{await page.waitForFunction(()=>document.querySelector('select option[value="1"]'));await page.evaluate(()=>{const field=[...document.querySelectorAll('label')].find(l=>l.textContent.startsWith('Contact ID')).querySelector('input');const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;setter.call(field,'1');field.dispatchEvent(new Event('input',{bubbles:true}));});await page.select('form select','1');await page.type('form textarea','Browser queue fixture only');};
  await choose();await page.click('button[type="submit"]');await page.waitForSelector('[role="alert"]');
  check(await page.evaluate(()=>document.body.textContent.includes('Synthetic lost acknowledgement')),mode+' lost acknowledgement explained');
  await page.click('button[type="submit"]');await page.waitForFunction(()=>document.body.textContent.includes('Actual state: queued'));
  const posts=requests.filter(r=>r.method==='POST'&&r.route==='/send/');check(posts.length===2,mode+' deliberate retry only');check(posts[0].key===posts[1].key&&posts[0].body===posts[1].body,mode+' stable idempotency and frozen payload');
  check(messages.size===1,mode+' fixture queue deduplicates retry');check(!await page.evaluate(()=>document.body.textContent.includes('1 sent')),mode+' no fictional sent claim');
  check(await page.evaluate(()=>{const p=JSON.parse(sessionStorage.getItem('sender-suite.pending-sms.v1'));return p.messageId===1&&!('body' in p);}),mode+' recovery identity without stored SMS text');
  page.once('dialog',dialog=>dialog.accept());await page.evaluate(()=>{[...document.querySelectorAll('button')].find(b=>b.textContent==='Cancel before claim').click();});await page.waitForFunction(()=>document.body.textContent.includes('Actual state: cancelled'));
  check(requests.some(r=>r.method==='POST'&&r.route==='/send/messages/1/cancel'),mode+' explicit preclaim cancellation route');
  await page.reload({waitUntil:'networkidle0'});await page.waitForSelector('[aria-label="Native SMS queue"]');check(await page.$eval('button[type="submit"]',el=>el.disabled),mode+' reload cannot silently submit another request');check(requests.filter(r=>r.method==='POST'&&r.route==='/send/').length===2,mode+' reload does not resend');
  check(!requests.some(r=>r.url.includes('/api/v1')||r.url.includes('/sw.js')),mode+' no original backend or root service worker calls');check(await page.evaluate(()=>location.pathname==='/sub/'&&location.search==='?sender_suite_app=1'),mode+' website mount path preserved');
  check(faults.length===0,mode+' no uncaught browser errors: '+faults.join('; '));await context.close();
 }
 const evidence={ok:true,assertions,browser:'Chromium '+await browser.version(),modes:['pretty','plain','disabled'],original_backend_used:false,provider_messages_sent:0,native_wordpress_verified:false,native_mysql_verified:false,scope:'Actual built copied UI in Chromium against a synthetic local native-REST fixture and actual PHP-rendered shell/CSP. Login, contacts, explicit queue, ambiguous acknowledgement/key retry, cancellation and reload checks. NOT a real WordPress/MySQL browser or complete application qualification.'};fs.writeFileSync(path.join(wp,'tests/native-ui-browser.json'),JSON.stringify(evidence,null,2)+'\n');console.log(JSON.stringify(evidence));
}finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
