class SQLException extends Exception {
}

class Statement {

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

public class Connection {

  public void prepareStatement(String s) throws SQLException {
    if (s.contains("<bad/>")) {
      assert s.indexOf("<bad/>") >= 0;
    }
  }

  public void close() throws SQLException {}

  public Statement createStatement() {
    return new Statement();
  }
}
