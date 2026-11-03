import java.util.Random;

public class boolean2 {
  public static boolean fun() {
    boolean b = new Random().nextBoolean();
    boolean result = f(b);
    return result == !b;
  }

  public static boolean f(boolean b) {
    return !b;
  }
}
