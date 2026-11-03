class SQLException extends Exception {
}

public class Statement {

  public void execute(String s) throws SQLException {
    if (s.contains("<bad/>")) {
      assert s.indexOf("<bad/>") >= 0;
    }
  }

  public void executeUpdate(String s, Object... o) throws SQLException {
    if (s.contains("<bad/>")) {
      assert s.indexOf("<bad/>") >= 0;
    }
  }

  public void executeQuery(String s) {
    if (s.contains("<bad/>")) {
      assert s.indexOf("<bad/>") >= 0;
    }
  }
}
