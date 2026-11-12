'use strict';
// Test-only egress guard; dependency downloads run separately without this guard.
const net=require('node:net'),dns=require('node:dns');const allowed=h=>!h||['localhost','127.0.0.1','::1','0.0.0.0'].includes(String(h));
const connect=net.Socket.prototype.connect;net.Socket.prototype.connect=function(...args){const x=Array.isArray(args[0])?args[0][0]:args[0];const host=x&&typeof x==='object'?(x.host||x.hostname):(typeof args[1]==='string'?args[1]:undefined);if(!allowed(host))throw new Error('Offline test: external socket denied');return connect.apply(this,args);};
const lookup=dns.lookup;dns.lookup=function(host,...args){if(!allowed(host))throw new Error('Offline test: external DNS denied');return lookup.call(this,host,...args);};
if(dns.promises){const original=dns.promises.lookup;dns.promises.lookup=async function(host,...args){if(!allowed(host))throw new Error('Offline test: external DNS denied');return original.call(this,host,...args);};}
