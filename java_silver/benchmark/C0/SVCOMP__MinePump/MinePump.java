class Environment {

  public enum WaterLevelEnum {
    low,
    normal,
    high
  }

  private WaterLevelEnum waterLevel = WaterLevelEnum.normal;

  private boolean methaneLevelCritical = false;

  void lowerWaterLevel() {
    switch (waterLevel) {
      case high:
        waterLevel = WaterLevelEnum.normal;
        break;
      case normal:
        waterLevel = WaterLevelEnum.low;
        break;
    }
  }

  public void waterRise() {
    switch (waterLevel) {
      case low:
        waterLevel = WaterLevelEnum.normal;
        break;
      case normal:
        waterLevel = WaterLevelEnum.high;
        break;
    }
  }

  public void changeMethaneLevel() {
    methaneLevelCritical = !methaneLevelCritical;
  }

  public boolean isMethaneLevelCritical() {
    return methaneLevelCritical;
  }

  @Override
  public String toString() {
    return "Env(Water:" + waterLevel + ",Meth:" + (methaneLevelCritical ? "CRIT" : "OK") + ")";
  }

  public WaterLevelEnum getWaterLevel() {
    return waterLevel;
  }
}

public class MinePump {

  boolean pumpRunning = false;

  boolean systemActive = true;

  Environment env;

  public MinePump(Environment env) {
    super();
    this.env = env;
  }

  public void timeShift() {
    if (pumpRunning) env.lowerWaterLevel();
    if (systemActive) processEnvironment();
  }

  private void processEnvironment__wrappee__base() {}

  public void processEnvironment() {
    if (pumpRunning && isMethaneAlarm()) {
      deactivatePump();
    } else {
      processEnvironment__wrappee__base();
    }
  }

  void activatePump() {
    pumpRunning = true;
  }

  public boolean isPumpRunning() {
    return pumpRunning;
  }

  void deactivatePump() {
    pumpRunning = false;
  }

  boolean isMethaneAlarm() {
    return env.isMethaneLevelCritical();
  }

  @Override
  public String toString() {
    return "Pump(System:"
        + (systemActive ? "On" : "Off")
        + ",Pump:"
        + (pumpRunning ? "On" : "Off")
        + ") "
        + env.toString();
  }

  public Environment getEnv() {
    return env;
  }

  public void stopSystem() {
    if (pumpRunning) {
      deactivatePump();
    }
    assert !pumpRunning;
    systemActive = false;
  }

  public void startSystem() {
    assert !pumpRunning;
    systemActive = true;
  }

  public boolean isSystemActive() {
    return systemActive;
  }

}
