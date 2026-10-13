import {defineConfig,devices} from '@playwright/test';
export default defineConfig({testDir:'./tests',fullyParallel:false,workers:1,reporter:'list',use:{baseURL:'http://localhost:3000',trace:'retain-on-failure'},projects:[
  {name:'desktop-chromium',use:{...devices['Desktop Chrome']}},
  {name:'iphone-webkit',use:{...devices['iPhone 13']}},
  {name:'ipad-webkit',use:{...devices['iPad Pro 11']}}
]});
