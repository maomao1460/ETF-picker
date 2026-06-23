// 作者：相空
import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom';
import Dashboard from './pages/Dashboard';
import Rankings from './pages/Rankings';
import EtfDetail from './pages/EtfDetail';
import Holdings from './pages/Holdings';
import History from './pages/History';
import Backtest from './pages/Backtest';
import './index.css';

function App() {
  return (
    <BrowserRouter>
      <div className="app-layout">
        <aside className="sidebar">
          <div className="sidebar-logo">
            <h1>📊 ETF轮动</h1>
            <span>赛道轮动策略系统</span>
          </div>
          <nav>
            <NavLink to="/" end>
              <span className="icon">🏠</span> 操作台
            </NavLink>
            <NavLink to="/rankings">
              <span className="icon">📋</span> 赛道排名
            </NavLink>
            <NavLink to="/etf">
              <span className="icon">📈</span> ETF详情
            </NavLink>
            <NavLink to="/holdings">
              <span className="icon">💼</span> 持仓管理
            </NavLink>
            <NavLink to="/history">
              <span className="icon">🕐</span> 历史信号
            </NavLink>
            <NavLink to="/backtest">
              <span className="icon">🧪</span> 回测中心
            </NavLink>
          </nav>
        </aside>

        <main className="main-content">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/rankings" element={<Rankings />} />
            <Route path="/etf" element={<EtfDetail />} />
            <Route path="/etf/:code" element={<EtfDetail />} />
            <Route path="/holdings" element={<Holdings />} />
            <Route path="/history" element={<History />} />
            <Route path="/backtest" element={<Backtest />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}

export default App;
