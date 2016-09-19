
import random
import sys
import math


IND_S = 0.0
IND_N = 1000.0
IND_W = 0.0
IND_E = 2000.0
BIN_PREC = 1e-6


class Oracle:
    calls = 0



def get_NN(FILENAME, lng, lat):
    Oracle.calls += 1
    slat = 0
    slng = 0
    mindist = 1e99
    objf = open("data/" + FILENAME, "r")
    #objf = open("objects2.txt", "r")
    while (True):
        lines = objf.readline()
        if not lines:
            break
        coord = lines.split()

        slng = float(coord[0])
        slat = float(coord[1])

        dist = eudist(slng, slat, lng, lat)

        if (dist > 0 and dist <= mindist):
            mindist = dist
            minlat = slat
            minlng = slng
    objf.close()
    ##print "Nearest Neighbour"
    return minlng, minlat


def mid_point(x1, y1, x2, y2):
    return round(((float)(x1 + x2) / 2), 2), round(((float)(y1 + y2) / 2), 2)


def  perp_dir(x1, y1, x2, y2):
    if (y2 != y1):
        return round(((float)(x2 - x1) / (y1 - y2)), 2)
    return 1e99


def collinear(x1, y1, x2, y2, x3, y3):
    if (x1 == x2 and x2 == x3):
        return True
    if (y1 == y2 and y2 == y3):
        return True

    if (x1 == x2 and x2 != x3):
        return False
    if (x1 != x2 and x2 == x3):
        return False
    if (x1 == x3 and x2 != x3):
        return False

    if (y1 == y2 and y2 != y3):
        return False
    if (y1 != y2 and y2 == y3):
        return False
    if (y1 == y3 and y2 != y3):
        return False

    m1 = round((float(y2 - y1) / (x2 - x1)), 2)
    m2 = round((float(y3 - y1) / (x3 - x1)), 2)

    if (m1 == m2):
        return True
    return False


def new_dir(FILENAME, aplng, aplat, alng, alat, dlng, dlat):
    if (alng is dlng):
        m1 = 1e99
    else:
        m1 = float(alat - dlat) / (alng - dlng)

    a1 = math.atan(m1)
    #print "ANGLE A1", a1
    c1x = -1.1
    c1y = -2.5
    c2x = -3.3
    c2y = -5.7

    tth = 0.5
    th = math.atan(tth)
    #print "TH = ", th
    while(True):
        #print "NewDirection"
        ac1 = a1 + th
        ac2 = ac1 + th
        ##print "C ANGLES=", ac1, ac2
        tac1 = math.tan(ac1)
        tac2 = math.tan(ac2)

        Bc1 = isect_B(dlng, dlat, tac1)
        ##print "Bc1", Bc1
        Bc2 = isect_B(dlng, dlat, tac2)
        ##print "Bc2", Bc2

        Bc1x, Bc1y = find_CXY(Bc1, aplng, aplat)
        Bc2x, Bc2y = find_CXY(Bc2, aplng, aplat)
        ##print "C BORDERS=", Bc1x, Bc1y, Bc2x, Bc2y
        c1x, c1y = bin_search(FILENAME, Bc1x, Bc1y, dlng, dlat, dlng, dlat)
        c2x, c2y = bin_search(FILENAME, Bc2x, Bc2y, dlng, dlat, dlng, dlat)
        ##print "INT C1, C2", c1x, c1y, c2x, c2y

        th /= 2
        if (collinear(alng, alat, c1x, c1y, c2x, c2y) is True):
            break

    if (c1x == alng):
        return 1e99
    ##print "C1 C2 A = ", c1x, c1y, c2x, c2y, alng, alat
    m = float(c1y - alat) / (c1x - alng)
    ##print "SLOPE=", m
    m_ = round(m, 2)
    if (m_ == -0.0):
        m_ = 0.0
    return m_


def isect(x1, y1, x2, y2, x3, y3, x4, y4):

    if (x1 == x2 and x3 == x4):
        return -1, -1

    if (x1 == x2 and x3 != x4):
        m = float(y4 - y3) / (x4 - x3)
        y_test = m * (x1 - x3) + y3
        if (y1 >= y_test and y2 <= y_test) or (y1 <= y_test and y2 >= y_test):
            if (y3 >= y_test and y4 <= y_test) or (y3 <= y_test and y4 >= y_test):
                return x1, y_test
        return -1, -1

    if (x1 != x2 and x3 == x4):
        m = float(y2 - y1) / (x2 - x1)
        y_test = m * (x3 - x1) + y1
        if (y1 >= y_test and y2 <= y_test) or (y1 <= y_test and y2 >= y_test):
            if (y3 >= y_test and y4 <= y_test) or (y3 <= y_test and y4 >= y_test):
                return x3, y_test
        return -1, -1

    m1 = (float)(y2 - y1) / (x2 - x1)
    m2 = (float)(y4 - y3) / (x4 - x3)

    if (m1 == m2):
        return -1, -1

    c1 = y1 - m1 * x1
    c2 = y3 - m2 * x3

    x_test = float(c2 - c1) / (m1 - m2)
    y_test1 = m1 * (x_test - x1) + y1
    y_test2 = m2 * (x_test - x3) + y3

    if ((x1 >= x_test and x2 <= x_test) or (x1 <= x_test and x2 >= x_test)):
        if (x3 >= x_test and x4 <= x_test) or (x3 <= x_test and x4 >= x_test):
            if ((y1 >= y_test1 and y2 <= y_test1) or (y1 <= y_test1 and y2 >= y_test1)):
                if (y3 >= y_test2 and y4 <= y_test2) or (y3 <= y_test2 and y4 >= y_test2):
                    return round(x_test, 2), round(y_test1, 2)
    return -1, -1


def isect_B(alng, alat, dirn):
    ret = []
    if (dirn == 1e99):
        ret.append(alng)
        ret.append(IND_N)
        ret.append(alng)
        ret.append(IND_S)
        return ret
    elif (dirn == 0):
        ret.append(IND_W)
        ret.append(alat)
        ret.append(IND_E)
        ret.append(alat)
        return ret
    else:
        xt = (float)((IND_N - alat) / dirn) + alng
        xb = (float)((IND_S - alat) / dirn) + alng
        yr = (dirn * (IND_E - alng)) + alat
        yl = (dirn * (IND_W - alng)) + alat

    if (xt <= IND_E and xt >= IND_W):
        ret.append(xt)
        ret.append(IND_N)
    if (xb <= IND_E and xb >= IND_W):
        ret.append(xb)
        ret.append(IND_S)

    if (yl <= IND_N and yl >= IND_S):
        ret.append(IND_W)
        ret.append(yl)
    if (yr <= IND_N and yr >= IND_S):
        ret.append(IND_E)
        ret.append(yr)

    if (len(ret) == 4):
        return ret
    else:
        #print "Error: Intersection with B"
        return -1


def eudist(x1, y1, x2, y2):
    return math.sqrt(((x1 - x2) ** 2) + ((y1 - y2) ** 2))


def bin_search(FILENAME, x1, y1, x2, y2, dlng, dlat):

    xm = -1
    ym = -1
    ##print "BINSEARCH BVALUES=", x1, y1
    ##print "BINSEARCH AVALUES=", x2, y2

    while(eudist(x1, y1, x2, y2) > BIN_PREC):

        xm = float(x1 + x2) / 2
        ym = float(y1 + y2) / 2
        ##print "binsearch...", xm, ym
        lg, lt = get_NN(FILENAME, xm, ym)
        ##print "CLOSEST OBJECT ", lg, lt
        d1 = round(eudist(lg, lt, xm, ym), 2)
        d2 = round(eudist(xm, ym, dlng, dlat), 2)
        if(d1 == d2):
            x2 = xm
            y2 = ym
            ##print "INSIDE"
        else:
            x1 = xm
            y1 = ym
            ##print "OUTSIDE"
        ##print "BIN"

    if(xm != -1 and ym != -1):
        xm = round(xm, 2)
        ym = round(ym, 2)
        if (xm == -0.0):
            xm = 0.0
        if (ym == -0.0):
            ym = 0.0
        ##print "BINSEARCH  RESULTS=", xm, ym
        return xm, ym
    else:
        #print "ERROR IN BINARY SEARCH"
        #print xm, ym
        sys.exit()


def find_CXY(B, dlng, dlat):
    x1 = B[0]
    y1 = B[1]
    x2 = B[2]
    y2 = B[3]
    x3 = dlng
    y3 = dlat

    n = ((y2 - y1) * (x3 - x1) - (x2 - x1) * (y3 - y1))
    d = ((y2 - y1) ** 2 + (x2 - x1) ** 2)
    k = (float)(n) / d
    x4 = x3 - k * (y2 - y1)
    y4 = y3 + k * (x2 - x1)

    ##print x3, y3, x4, y4
    if (x4 > x3):
        if (y2 < y1):
            By = y2
            Bx = x2
        else:
            By = y1
            Bx = x1
    elif (x4 < x3):
        if (y2 > y1):
            By = y2
            Bx = x2
        else:
            By = y1
            Bx = x1
    elif (x4 == x3):
        if (y4 > y3):
            if (x1 > x2):
                Bx = x1
                By = y1
            else:
                Bx = x2
                By = y2
        elif (y4 < y3):
            if (x1 < x2):
                Bx = x1
                By = y1
            else:
                Bx = x2
                By = y2
    return Bx, By


def find_BXY(B, dlng, dlat):
    x1 = B[0]
    y1 = B[1]
    x2 = B[2]
    y2 = B[3]
    x3 = dlng
    y3 = dlat

    n = ((y2 - y1) * (x3 - x1) - (x2 - x1) * (y3 - y1))
    d = ((y2 - y1) ** 2 + (x2 - x1) ** 2)
    k = (float)(n) / d
    x4 = x3 - k * (y2 - y1)
    y4 = y3 + k * (x2 - x1)

    if (x4 > x3):
        if (y2 > y1):
            By = y2
            Bx = x2
        else:
            By = y1
            Bx = x1
    elif (x4 < x3):
        if (y2 < y1):
            By = y2
            Bx = x2
        else:
            By = y1
            Bx = x1
    elif (x4 == x3):
        if (y4 > y3):
            if (x1 < x2):
                Bx = x1
                By = y1
            else:
                Bx = x2
                By = y2
        elif (y4 < y3):
            if (x1 > x2):
                Bx = x1
                By = y1
            else:
                Bx = x2
                By = y2
    return Bx, By


def find_a1(FILENAME, alng, alat, dlng, dlat, dirn):
    B = isect_B(alng, alat, dirn)
    #print "B VECTOR", B
    #print alng, alat
    if ((alng, alat) == (B[0], B[1])):
        Bx = B[2]
        By = B[3]
    elif ((alng, alat) == (B[2], B[3])):
        Bx = B[0]
        By = B[1]
    #elif ((B[2], B[3]) is (0, 8.74)):
        #Bx = 10
        #By = 8.74
        #print "HAHAHAHAHAHAHAHAHAHh", Bx, By
    else:
        t1, t2 = find_BXY(B, dlng, dlat)
        Bx = t1
        By = t2
        #print "B POINTS", Bx, By
    return bin_search(FILENAME, Bx, By, alng, alat, dlng, dlat)


def polygon_area(alng, alat):
    # calculate area
    area = 0
    for i in range(len(alat) - 1):
        area += alat[i] * alng[i + 1] - alat[i + 1] * alng[i]
    area += alat[i] * alng[0] - alat[0] * alng[i]
    area = (float)(area / 2)
    if(area < 0):
        area *= -1
    return round(area, 2)


def find_area(FILENAME, dlng, dlat):

    elng, elat = get_NN(FILENAME, dlng, dlat)
    alng, alat = mid_point(dlng, dlat, elng, elat)
    dirn = perp_dir(elng, elat, dlng, dlat)
    Oracle.calls = 0
    #print "E_POINT= ", elng, elat
    #print "A_POINT= ", alng, alat

    e0g = elng
    e0t = elat

    ag = []
    at = []
    ag.append(alng)
    at.append(alat)
    i = 0
    #print "VERTEX ADDED=", i, ag[i], at[i]
    d = []
    while (True):
        ag.append(0)
        at.append(0)
        #print "COUNTER========================================", i

        a_g, a_t = find_a1(FILENAME,
            ag[i], at[i], dlng, dlat, dirn)
        if (get_NN(FILENAME, a_g, a_t) == dlng, dlat):
            ag[i + 1] = a_g
            at[i + 1] = a_t
            #print "VERTEX ADDED=", i + 1, ag[i + 1], at[i + 1]
        else:
            print("ERROR ADDING NODE")
            sys.exit()

        dirn1 = new_dir(FILENAME,
            ag[i], at[i], ag[i + 1], at[i + 1], dlng, dlat)

        d.append(dirn1)

        #print "NEW DIR=", dirn1
        ##print "NEW E_POINT=", enlng, enlat
        #elng = enlng
        #elat = enlat
        if (i > 2):
            #if (dirn in d):
                #break
            #if (at[i + 1] == at[i] and ag[i + 1] == ag[i]):
                #break
            if (dirn == dirn1):
                break

            #if(termin(ag[i + 1], at[i + 1], dirn1, e0g, e0t, d0)is True):
                #break
            fin_isect = isect(
                ag[i + 1], at[i + 1], ag[i], at[i], e0g, e0t, dlng, dlat)

            #print "FIN_ISECT=", fin_isect
            if (fin_isect != (-1, -1) or i == 10):
                break

        dirn = dirn1
        i += 1
        #print
        #print
    #print ag
    #print at
    area = polygon_area(ag, at)
    #print "VERTICES= ", len(ag)
    ##print ag
    ##print at
    return area, len(ag)


def get_sum(FILENAME, N1):
    #geocode(address="Dominos+HIMALAYA+MALL,+AHMADABAD+Ahmedabad",
    #sensor="false")
    #for count in range(50):
    Oracle.calls = 0
    S = []
    #N = int(math.sqrt(N1)) + 1
    N = N1
    #N = N1
    #print "Num of P::", N
    max_edges = 0
    avg_edges = 0
    sum_edges = 0
    for i in range(0, N):
        plng = random.uniform(IND_W, IND_E)
        plat = random.uniform(IND_S, IND_N)
        #print "P_OBJECT=", plng, plat
        dlng, dlat = get_NN(FILENAME, plng, plat)
        #print "D_OBJECT=", dlng, dlat

        area, v_edges = find_area(FILENAME, dlng, dlat)
        #print "AREA = ", area

        S.append(0)
        if (area != 0):
            S[i] = ((IND_N - IND_S) * (IND_E - IND_W)) / area
            sum_edges += v_edges
            if (v_edges > max_edges):
                max_edges = v_edges
        else:
            #print "AREA IS ZERO", i
            N -= 1
        #print
        #print
        #print

    Sum = 0
    for i in range(0, N):
        Sum += S[i] / N
    if(N != 0):
        avg_edges = sum_edges / N
    #print "SUM = ", Sum

    #print ".",
    if (N1 * 0.5 <= Sum and Sum <= N1 * 1.5):
        if (Sum <= N1):
            print int(Sum) + 1, max_edges, avg_edges, Oracle.calls
            return int(Sum) + 1, max_edges, avg_edges
        elif (Sum >= N1):
            print int(Sum), max_edges, avg_edges, Oracle.calls
            return int(Sum), max_edges, avg_edges
    else:
        return get_sum(FILENAME, N1)


if __name__ == '__main__':
    uni = "uni"
    gau = "gau"
    tri = "tri"

    FNAME = "data"
    SNAME = "sum"
    FEXT = ".txt"
    #print "Uniform"
    #for i in (5, 10, 50):
        #FILENAME = FNAME + uni + str(i) + FEXT
        #OUTNAME = SNAME + uni + str(i) + FEXT
        #f = open("sumdir/" + OUTNAME, "w")
        #print OUTNAME
        #for j in range(0, 10):
            #unisum, max_e, avg_e = get_sum(FILENAME, i)
            #f.write(str(unisum) + " " + str(max_e) + " " + str(avg_e))
            #f.write('\n')
        #f.close()
    #print "-------------------"
    #print "Normal"
    #for i in (5, 10, 50):
        #FILENAME = FNAME + gau + str(i) + FEXT
        #OUTNAME = SNAME + gau + str(i) + FEXT
        #f = open("sumdir/" + OUTNAME, "w")
        #print OUTNAME
        #for j in range(0, 10):
            #gausum, max_e, avg_e = get_sum(FILENAME, i)
            #f.write(str(gausum) + " " + str(max_e) + " " + str(avg_e))
            #f.write('\n')
        #f.close()
    #print "-------------------"
    #print "Trianle"
    #for i in (10, 10):
        #FILENAME = FNAME + tri + str(i) + FEXT
        #OUTNAME = SNAME + tri + str(i) + FEXT
        #f = open("sumdir/" + OUTNAME, "w")
        #print OUTNAME
        #for j in range(0, 10):
            #trisum, max_e, avg_e = get_sum(FILENAME, i)
            #f.write(str(trisum) + " " + str(max_e) + " " + str(avg_e))
            #f.write('\n')
        #f.close()
    #print "-------------------"



##print collinear(1047.72, 1000.0, 993.34, 1000.0, 1135.52, 1000.0)
##print collinear(779.46, 796.48, 870.2, 740.49, 591.12, 912.69)
##print collinear(780.16, 796.04, 870.69, 740.19, 593.21, 911.4)
