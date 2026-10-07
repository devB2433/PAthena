import React from 'react';
import ReactDOM from 'react-dom/client';
import {ConfigProvider} from 'antd';
import zhCN from 'antd/locale/zh_CN';
import App from './App';
import './style.css';
ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><ConfigProvider locale={zhCN} theme={{token:{colorPrimary:'#176e67',colorText:'#19324b',colorBgLayout:'#f3f6fa',borderRadius:5,fontFamily:'"PingFang SC", "Microsoft YaHei", sans-serif'}}}><App/></ConfigProvider></React.StrictMode>);
