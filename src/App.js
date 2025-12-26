import React from "react";
import "./App.css";
import ChatWindow from "./components/ChatWindow";
import logo from "./assets/pslogo.svg";
import logoMobile from "./assets/ps-logo-mobile.svg";

function App() {

  return (
    <div className="App">
      <div className="heading">
        <a href="https://www.partselect.com" target="_blank" rel="noopener noreferrer" className="header-logo-link">
          <img src={logo} alt="PartSelect" className="header-logo" />
        </a>
        <div className="header-center">
          <img src={logoMobile} alt="" className="header-icon" />
          <span className="header-text">Assistant</span>
        </div>
        <a href="https://www.partselect.com/Contact/#OrderPh" target="_blank" rel="noopener noreferrer" className="header-contact">
          <div className="header-contact-phone">1-866-319-8402</div>
          <div className="header-contact-hours">Monday to Saturday<br />8am - 8pm EST</div>
        </a>
      </div>
        <ChatWindow/>
    </div>
  );
}

export default App;
