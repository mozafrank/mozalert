
var $driver = require('selenium-webdriver');

const firefox = require('selenium-webdriver/firefox');

const _ = require('lodash');

var options = new firefox.Options();

options.addArguments("-headless");
options.addArguments("--window-size=1024,768");
options.addArguments("--disable-gpu");
options.addArguments("--test-type");
options.addArguments("--no-sandbox");
options.addArguments("--disable-dev-shm-usage");

var $browser = new $driver.Builder()
    .forBrowser('firefox')
    .setFirefoxOptions(options)
    .build();

var $secure = {}
_.each(process.env, (val,key) => {
    $secure[key] = val;
});

$browser.waitForElement = function (locatorOrElement, timeoutMsOpt) {
        return $browser.wait($driver.until.elementLocated(locatorOrElement), timeoutMsOpt || 1000, 'Timed-out waiting for element to be located using: ' + locatorOrElement);
};

$browser.waitForAndFindElement = function (locatorOrElement, timeoutMsOpt) {
      return $browser.waitForElement(locatorOrElement, timeoutMsOpt).then(function (element) {
          return $browser.wait($driver.until.elementIsVisible(element), timeoutMsOpt || 1000, 'Timed-out waiting for element to be visible using: ' + locatorOrElement).then(function () {
                return element;
          });
      });
};

module.exports = function() {
	this.$browser = $browser;
	this.$driver = $driver;
	this.$secure = $secure;
}

